"""Application workloads generator & deployment service (CCTV, Physical AI, OTT, IoT, Custom)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import yaml

from app.schemas import (
    AppDeployResponse,
    AppUndeployResponse,
    PlSolveResponse,
    ProfileRecord,
    SliceApplicationConfig,
)
from app.services import app_images, cluster_status, cctv_videos, ip_allocator, multus_iface, profile_store, ran_workloads, site_ips
from app.services.cmd_stream import error_event, log_event, result_event, status_event

def resolve_target_cluster(
    app_cfg: SliceApplicationConfig,
    pl_result: Optional[PlSolveResponse] = None,
) -> str:
    """Determine target cluster for application server."""
    explicit = (app_cfg.target_cluster or "").lower()
    if explicit in ("central", "regional", "edge"):
        return explicit

    # Slice 2 Physical AI is bound to edge gpu-a40. Do not let PL move it.
    if (app_cfg.app_type or "").lower() == "physical_ai":
        return "edge"

    # If auto, check PL placement result
    if pl_result is not None:
        deploy_map = getattr(pl_result, "deploy_map", None) or {}
        sid_str = str(app_cfg.slice_id)
        site_from_id = {0: "edge", 1: "regional", 2: "central"}
        if sid_str in deploy_map:
            place = deploy_map[sid_str]
            site_app = str(getattr(place, "app", "") or "").lower()
            if site_app not in ("edge", "regional", "central"):
                app_id = getattr(place, "app_id", None)
                if isinstance(place, dict):
                    site_app = str(place.get("app") or "").lower()
                    app_id = place.get("app_id", app_id)
                site_app = site_from_id.get(app_id, site_app)
            if site_app in ("edge", "regional", "central"):
                return site_app

        if getattr(pl_result, "slices", None):
            for s in pl_result.slices:
                if s.id == app_cfg.slice_id and getattr(s, "placement", None):
                    site_app = getattr(s.placement, "app", "").lower()
                    if site_app in ("edge", "regional", "central"):
                        return site_app

        ip_plan = getattr(pl_result, "ip_plan", None)
        if ip_plan and getattr(ip_plan, "slices", None):
            for s in ip_plan.slices:
                if s.slice_id == app_cfg.slice_id and getattr(s, "site_app", None):
                    site_app = s.site_app.lower()
                    if site_app in ("edge", "regional", "central"):
                        return site_app

    # Heuristic defaults based on application type
    if app_cfg.app_type == "cctv":
        return "regional"
    elif app_cfg.app_type == "ott":
        return "central"
    elif app_cfg.app_type == "iot":
        return "central"
    return "central"


def _physical_ai_gpu_arch(target_cluster: str, params: Dict[str, Any]) -> str:
    """Node arch follows the site GPU. Saved gpu_arch=arm64-gh200 must not pin edge A40."""
    cluster = (target_cluster or "").lower()
    raw = str((params or {}).get("gpu_arch") or "auto").lower()
    if cluster == "edge":
        return "amd64"
    if cluster in ("regional", "central"):
        return "arm64"
    if raw in ("amd64", "amd64-a40", "a40", "x86_64"):
        return "amd64"
    if raw in ("arm64", "arm64-gh200", "gh200", "aarch64"):
        return "arm64"
    return "amd64"


PHYSICAL_AI_MODEL = "nvidia/Cosmos3-Nano"


def _physical_ai_model(params: Dict[str, Any]) -> str:
    raw = str((params or {}).get("model") or "").strip()
    if not raw or "Nemotron" in raw:
        return PHYSICAL_AI_MODEL
    return raw


def _physical_ai_server_image(image: Optional[str], gpu_arch: str) -> str:
    """Pick the platform tag for this site. Rewrite stale arm64 tags on edge A40."""
    img = app_images.resolve_server_image("physical_ai", image)
    repo, sep, tag = img.rpartition(":")
    if not sep or not tag.startswith("nws-v"):
        return img
    if gpu_arch == "amd64":
        return f"{repo}:nws-v0.7-amd64"
    return f"{repo}:nws-v0.7-arm64-cu128"


CCTV_UE_CONSOLE_IMAGE = "10.1.132.30:5000/cctv-ue-console:nws-v0.1-amd64"
CCTV_CONSOLE_PORT = site_ips.UE_CONSOLE_PORT
CCTV_BACKEND_PORT = 8090


def cctv_console_ip(client_index: int, slice_id: int = 1) -> str:
    return site_ips.ue_console_ip(slice_id, client_index)


def cctv_console_mac(slice_id: int, client_index: int) -> str:
    return site_ips.ue_console_mac(slice_id, client_index)


PHYSICAL_AI_UE_CONSOLE_IMAGE = "10.1.132.30:5000/cosmo3-ue-console:nws-v0.18-amd64"
PHYSICAL_AI_CONSOLE_PORT = site_ips.UE_CONSOLE_PORT
PHYSICAL_AI_BACKEND_PORT = 8090


def physical_ai_console_ip(client_index: int, slice_id: int = 2) -> str:
    return site_ips.ue_console_ip(slice_id, client_index)


def physical_ai_console_mac(slice_id: int, client_index: int) -> str:
    return site_ips.ue_console_mac(slice_id, client_index)


IOT_MOSQUITTO_IMAGE = "10.1.132.30:5000/iot-mosquitto:nws-v0.1-amd64"
IOT_MOSQUITTO_CONTROL_PORT = 1884
IOT_UE_CONSOLE_IMAGE = "10.1.132.30:5000/iot-ue-console:nws-v0.10-amd64"
IOT_CONSOLE_PORT = site_ips.UE_CONSOLE_PORT
IOT_BACKEND_PORT = 8090


def iot_console_ip(client_index: int, slice_id: int = 4) -> str:
    return site_ips.ue_console_ip(slice_id, client_index)


def iot_console_mac(slice_id: int, client_index: int) -> str:
    return site_ips.ue_console_mac(slice_id, client_index)


OTT_UE_CONSOLE_IMAGE = "10.1.132.30:5000/ott-ue-console:nws-v0.33-amd64"
OTT_CHROMIUM_IMAGE = os.environ.get(
    "OTT_CHROMIUM_IMAGE",
    "10.1.132.30:5000/linuxserver-chromium:latest",
)
OTT_CONSOLE_PORT = site_ips.UE_CONSOLE_PORT
OTT_BACKEND_PORT = 8090


def ott_console_ip(client_index: int, slice_id: int = 3) -> str:
    return site_ips.ue_console_ip(slice_id, client_index)


def ott_console_mac(slice_id: int, client_index: int) -> str:
    return site_ips.ue_console_mac(slice_id, client_index)


def server_deployment_name(app_cfg: SliceApplicationConfig) -> str:
    """Deployment name emitted by generate_server_manifests."""
    sid = app_cfg.slice_id
    app_type = (app_cfg.app_type or "none").lower()
    if app_type == "cctv":
        return "application-cctv"
    if app_type == "physical_ai":
        return "application-physical-ai"
    if app_type == "ott":
        return "application-ott"
    if app_type == "iot":
        return "application-iot"
    if app_type == "custom":
        return "application-custom"
    return f"slice{sid}-{app_type}"


def expected_app_deployments(cluster: str, rec: Any) -> List[str]:
    """Expected application *server* Deployment names on a cluster (GitOps).

    UE / client Deployments are on-demand and omitted from the status bar.
    """
    names: List[str] = []
    apps = getattr(rec, "applications", None) or {}
    pl_result = getattr(rec, "pl_result", None)
    gitops_live = bool(getattr(rec, "deployed", False))
    for cfg in apps.values():
        if cfg is None:
            continue
        app_type = (cfg.app_type or "none").lower()
        if not getattr(cfg, "enabled", True) or app_type == "none":
            continue
        target = resolve_target_cluster(cfg, pl_result)
        if gitops_live and target == cluster:
            names.append(server_deployment_name(cfg))
    return names


def _kube_cmd(cluster: str) -> List[str]:
    kubeconfig = cluster_status._kubeconfig_for(cluster)
    context = cluster_status._context_for(cluster)
    return ["kubectl", "--kubeconfig", kubeconfig, "--context", context]


def _influx_pusher_container(
    m_port: int,
    a_name: str,
    sid: int,
    profile_name: str,
    app_type: str,
    cluster: str,
    extra_env: Optional[list] = None,
    ctr_name: str = "metrics-exporter",
) -> dict:

    # Edge shares a cluster with Influx; the 10.1.137.104 site VIP is not
    # reachable over TCP from gpu-a40 (ICMP works, :8086 does not).
    influx_url = (
        "http://influxdb.influxdb.svc:8086" if cluster == "edge" else "http://10.1.137.104:8086"
    )
    route_setup = (
        ""
        if cluster == "edge"
        else "os.system(\"ip route replace 10.1.137.104/32 via $(ip route show default | awk '{print $3}') dev eth0 2>/dev/null || true\")\n"
    )
    pusher_code = (
        "import os, time, re, urllib.request, math, subprocess\n"
        + route_setup
        + "port = os.environ.get('METRICS_PORT', '9102')\n"
        f"url = os.environ.get('INFLUXDB_URL', '{influx_url}').rstrip('/')\n"
        "tok = os.environ.get('INFLUXDB_TOKEN', 'ina-infra-influxdb-token')\n"
        "org = os.environ.get('INFLUXDB_ORG', 'ina-infra')\n"
        "buc = os.environ.get('INFLUXDB_BUCKET', 'default')\n"
        "meas = os.environ.get('INFLUXDB_MEASUREMENT', 'application_metrics')\n"
        "sid = os.environ.get('SLICE_ID', '1')\n"
        "prof = os.environ.get('PROFILE_NAME', 'ina-infra')\n"
        "typ = os.environ.get('APP_TYPE', 'unknown')\n"
        "name = os.environ.get('APP_NAME', 'app')\n"
        "clu = os.environ.get('TARGET_CLUSTER', 'edge')\n"
        "w_url = f'{url}/api/v2/write?org={org}&bucket={buc}&precision=ns'\n"
        "headers = {'Authorization': f'Token {tok}', 'Content-Type': 'text/plain; charset=utf-8'}\n"
        "last_ue_lat = {}\n"
        "def _parse_offset_to_ms(text):\n"
        "  m = re.search(r'Offset:\\s*([+-]?[0-9.]+)\\s*(ns|us|µs|ms|s)\\b', text or '')\n"
        "  if not m: return None\n"
        "  v, u = float(m.group(1)), m.group(2)\n"
        "  mul = {'ns': 1e-6, 'us': 1e-3, 'µs': 1e-3, 'ms': 1.0, 's': 1000.0}[u]\n"
        "  return round(abs(v) * mul, 3)\n"
        "def ntp_offset_ms():\n"
        "  try:\n"
        "    p = subprocess.run(['chronyc','tracking'], capture_output=True, text=True, timeout=2)\n"
        "    if p.returncode == 0:\n"
        "      for line in p.stdout.splitlines():\n"
        "        if line.strip().startswith('Last offset'):\n"
        "          sec = float(line.split(':',1)[1].strip().split()[0])\n"
        "          return round(abs(sec)*1000.0, 3)\n"
        "  except Exception:\n"
        "    pass\n"
        "  try:\n"
        "    p = subprocess.run(['nsenter','-t','1','-m','--','timedatectl','timesync-status'], capture_output=True, text=True, timeout=3)\n"
        "    ms = _parse_offset_to_ms(p.stdout)\n"
        "    if ms is not None: return ms\n"
        "  except Exception:\n"
        "    pass\n"
        "  try:\n"
        "    import ctypes, ctypes.util\n"
        "    class T(ctypes.Structure):\n"
        "      _fields_=[('modes',ctypes.c_uint),('offset',ctypes.c_long),('freq',ctypes.c_long),('maxerror',ctypes.c_long),('esterror',ctypes.c_long),('status',ctypes.c_int),('constant',ctypes.c_long),('precision',ctypes.c_long),('tolerance',ctypes.c_long),('time_tv_sec',ctypes.c_long),('time_tv_usec',ctypes.c_long),('tick',ctypes.c_long),('ppsfreq',ctypes.c_long),('jitter',ctypes.c_long),('shift',ctypes.c_int),('stabil',ctypes.c_long),('jitcnt',ctypes.c_long),('calcnt',ctypes.c_long),('errcnt',ctypes.c_long),('stbcnt',ctypes.c_long),('tai',ctypes.c_int),('_pad',ctypes.c_int*11)]\n"
        "    libc=ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)\n"
        "    tx=T()\n"
        "    if libc.adjtimex(ctypes.byref(tx))<0: return None\n"
        "    sec = (tx.offset/1e9) if (tx.status & 0x2000) else (tx.offset/1e6)\n"
        "    return round(abs(sec)*1000.0, 3)\n"
        "  except Exception:\n"
        "    return None\n"
        "print(f'[metrics-exporter] Started pushing metrics for {name} to InfluxDB ({w_url})', flush=True)\n"
        "while True:\n"
        "  try:\n"
        "    fields = {}\n"
        "    is_client = ('client' in name)\n"
        "    koff = ntp_offset_ms()\n"
        "    if koff is not None:\n"
        "      if is_client:\n"
        "        fields['clock_offset_ue'] = koff\n"
        "      else:\n"
        "        fields['clock_offset_server'] = koff\n"
        "    per_client = {}\n"
        "    extra_url = os.environ.get('APP_METRICS_URL', '').strip()\n"
        "    scrape_urls = [extra_url] if extra_url else [f'http://127.0.0.1:{port}/metrics']\n"
        "    try:\n"
        "      txt = ''\n"
        "      for su in scrape_urls:\n"
        "        try:\n"
        "          req = urllib.request.Request(su)\n"
        "          with urllib.request.urlopen(req, timeout=3) as resp:\n"
        "            txt += resp.read().decode('utf-8', errors='replace') + '\\n'\n"
        "        except Exception as se:\n"
        "          print(f'[metrics-exporter] scrape {su}: {se}', flush=True)\n"
        "      for line in txt.splitlines():\n"
        "        line = line.strip()\n"
        "        if not line or line.startswith('#'): continue\n"
        "        m = re.match(r'^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\\{([^}]*)\\})?\\s+([+-]?(?:[0-9]*[.])?[0-9]+(?:[eE][+-]?[0-9]+)?)$', line)\n"
        "        if m:\n"
        "          k, lbls, v_str = m.groups()\n"
        "          try:\n"
        "            v = float(v_str)\n"
        "            if math.isnan(v) or math.isinf(v): continue\n"
        "            if k in ('app_ue_rtt_ms', 'rtt_ms', 'throughput_ul_mbps', 'throughput_dl_mbps', 'throughput_mbps', 'throughput_client'):\n"
        "              continue\n"
        "            ue_id = None\n"
        "            if lbls:\n"
        "              um = re.search(r'(?:ue_id|client)=\"([^\"]+)\"', lbls)\n"
        "              if um: ue_id = um.group(1)\n"
        "            if ue_id:\n"
        "              slot = per_client.setdefault(ue_id, {})\n"
        "              if k in ('app_ue_latency_ms', 'cctv_client_e2e_delay_ms'):\n"
        "                if v != 0 and last_ue_lat.get(ue_id) != v:\n"
        "                  last_ue_lat[ue_id] = v\n"
        "                  slot['latency_ms'] = v\n"
        "              elif k == 'cctv_client_net_delay_ms':\n"
        "                slot['net_delay_ms'] = v\n"
        "                if 'latency_ms' not in slot and v != 0 and last_ue_lat.get(ue_id) != v:\n"
        "                  last_ue_lat[ue_id] = v\n"
        "                  slot['latency_ms'] = v\n"
        "              elif k in ('app_ue_throughput_mbps', 'app_ue_throughput_dl_mbps'):\n"
        "                slot['app_throughput_mbps'] = v\n"
        "              elif k == 'app_ue_throughput_ul_mbps':\n"
        "                slot['app_throughput_ul_mbps'] = v\n"
        "              short_k = k.replace('cctv_client_', '').replace('cctv_', '')\n"
        "              if short_k == 'ingress_fps': short_k = 'application_ingress_fps'\n"
        "              if short_k == 'egress_fps': short_k = 'application_egress_fps'\n"
        "              slot[short_k] = v\n"
        "            if k == 'app_latency_ms':\n"
        "              continue\n"
        "            if k == 'app_throughput_mbps':\n"
        "              fields['app_throughput_mbps'] = v\n"
        "            if lbls:\n"
        "              lbl_clean = re.sub(r'[^a-zA-Z0-9_]', '_', lbls)\n"
        "              k = f'{k}_{lbl_clean}'\n"
        "            k_clean = re.sub(r'[^a-zA-Z0-9_]', '_', k)\n"
        "            if k_clean in ('app_ue_rtt_ms', 'rtt_ms', 'throughput_ul_mbps', 'throughput_dl_mbps', 'throughput_mbps', 'throughput_client'):\n"
        "              pass\n"
        "            else:\n"
        "              fields[k_clean] = v\n"
        "          except Exception:\n"
        "            pass\n"
        "      if fields.get('cctv_ue_clock_offset_seconds'):\n"
        "        fields['clock_offset_ue'] = round(fields['cctv_ue_clock_offset_seconds'] * 1000.0, 3)\n"
        "      if fields.get('cctv_clock_offset_seconds'):\n"
        "        fields['clock_offset_server'] = round(fields['cctv_clock_offset_seconds'] * 1000.0, 3)\n"
        "      for fk in list(fields):\n"
        "        if fk.startswith('application_clock_offset_ms') and float(fields[fk]):\n"
        "          fields['clock_offset_server'] = float(fields[fk])\n"
        "        if fk.startswith('application_throughput_bytes_per_sec') and 'throughput_server' not in fields:\n"
        "          fields['throughput_server'] = round(float(fields[fk]) * 8.0 / 1e6, 3)\n"
        "          if not is_client:\n"
        "            fields.setdefault('throughput_mbps', fields['throughput_server'])\n"
        "      if fields.get('latency_ms') == 0:\n"
        "        fields.pop('latency_ms', None)\n"
        "      if is_client:\n"
        "        for skip in ('throughput_ul_mbps', 'throughput_dl_mbps', 'throughput_mbps', 'throughput_client', 'app_ue_rtt_ms', 'rtt_ms'):\n"
        "          fields.pop(skip, None)\n"
        "      if is_client:\n"
        "        slot = per_client.setdefault(name, {})\n"
        "        for fk in ('latency_ms', 'app_ue_latency_ms'):\n"
        "          if fk in fields and fk not in slot:\n"
        "            slot[fk] = fields[fk]\n"
        "      if (not is_client) and typ == 'cctv':\n"
        "        keep = ('throughput_server', 'clock_offset_server', 'throughput_mbps', 'latency_ms', 'app_throughput_mbps', 'app_latency_ms')\n"
        "        fields = {k: v for k, v in fields.items() if k in keep or k.startswith('app_')}\n"
        "    except Exception as e:\n"
        "      print(f'[metrics-exporter] scrape error: {e}', flush=True)\n"
        "    lines_to_send = []\n"
        "    now_ns = time.time_ns()\n"
        "    orig = 'client' if is_client else 'server'\n"
        "    if fields:\n"
        "      ue_tag = f',ue_id={name}' if is_client else ''\n"
        "      tags = f'profile_name={prof},slice_id={sid},app_type={typ},app_name={name},cluster={clu},origin={orig}{ue_tag}'\n"
        "      f_str = ','.join(f'{k}={v}' for k, v in sorted(fields.items()) if not k.startswith('_') and isinstance(v, (int, float)))\n"
        "      if f_str:\n"
        "        lines_to_send.append(f'{meas},{tags} {f_str} {now_ns}')\n"
        "    for cl_name, cl_flds in per_client.items():\n"
        "      cl_orig = 'client' if is_client else 'server'\n"
        "      cl_tags = f'profile_name={prof},slice_id={sid},app_type={typ},app_name={cl_name},ue_id={cl_name},cluster={clu},origin={cl_orig}'\n"
        "      cl_f_str = ','.join(f'{k}={v}' for k, v in sorted(cl_flds.items()) if not k.startswith('_') and isinstance(v, (int, float)))\n"
        "      if cl_f_str:\n"
        "        lines_to_send.append(f'{meas},{cl_tags} {cl_f_str} {now_ns}')\n"
        "    if lines_to_send:\n"
        "      data = '\\n'.join(lines_to_send) + '\\n'\n"
        "      w_req = urllib.request.Request(w_url, data=data.encode('utf-8'), headers=headers, method='POST')\n"
        "      with urllib.request.urlopen(w_req, timeout=4) as w_resp:\n"
        "        pass\n"
        "  except Exception as e:\n"
        "    print(f'[metrics-exporter] loop error: {e}', flush=True)\n"
        "  time.sleep(1)\n"
    )
    container = {
        "name": ctr_name,
        "image": "docker.io/nicolaka/netshoot",
        "imagePullPolicy": "Always",

        "command": ["python3", "-c", pusher_code],
        "securityContext": {
            "privileged": True,
            "capabilities": {"add": ["NET_ADMIN"]},
        },
        "env": [
            {"name": "METRICS_PORT", "value": str(m_port)},
            {"name": "INFLUXDB_URL", "value": influx_url},
            {"name": "INFLUXDB_TOKEN", "value": "ina-infra-influxdb-token"},
            {"name": "INFLUXDB_ORG", "value": "ina-infra"},
            {"name": "INFLUXDB_BUCKET", "value": "default"},
            {"name": "INFLUXDB_MEASUREMENT", "value": "application_metrics"},
            {"name": "SLICE_ID", "value": str(sid)},
            {"name": "PROFILE_NAME", "value": profile_name},
            {"name": "APP_TYPE", "value": app_type},
            {"name": "APP_NAME", "value": a_name},
            {"name": "TARGET_CLUSTER", "value": cluster},
        ],
        "resources": {
            "requests": {"cpu": "20m", "memory": "32Mi"},
            "limits": {"cpu": "100m", "memory": "64Mi"},
        },
    }
    if extra_env:
        container["env"].extend(extra_env)
    return container


def _ue_influx_env(a_name: str, sid: int, profile_name: str, app_type: str, cluster: str) -> list:
    influx_url = (
        "http://influxdb.influxdb.svc:8086" if cluster == "edge" else "http://10.1.137.104:8086"
    )
    return [
        {"name": "INFLUXDB_URL", "value": influx_url},
        {"name": "INFLUXDB_TOKEN", "value": "ina-infra-influxdb-token"},
        {"name": "INFLUXDB_ORG", "value": "ina-infra"},
        {"name": "INFLUXDB_BUCKET", "value": "default"},
        {"name": "INFLUXDB_MEASUREMENT", "value": "application_metrics"},
        {"name": "SLICE_ID", "value": str(sid)},
        {"name": "PROFILE_NAME", "value": profile_name},
        {"name": "APP_TYPE", "value": app_type},
        {"name": "APP_NAME", "value": a_name},
        {"name": "TARGET_CLUSTER", "value": cluster},
    ]


def _rtt_probe_container(
    a_name: str,
    sid: int,
    profile_name: str,
    app_type: str,
    cluster: str,
    ping_target: str = "10.1.137.1",
    ping_iface: str = "oaitun_ue1",
) -> dict:
    """UE sidecar: ICMP RTT via dedicated rtt-probe image."""
    env = _ue_influx_env(a_name, sid, profile_name, app_type, cluster)
    env.append({"name": "RTT_PING_TARGET", "value": ping_target})
    env.append({"name": "RTT_PING_IFACE", "value": ping_iface})
    return {
        "name": "rtt-probe",
        "image": app_images.RTT_PROBE,
        "imagePullPolicy": "Always",
        "args": ["-I", ping_iface, ping_target],
        "securityContext": {
            "privileged": True,
            "capabilities": {"add": ["NET_RAW"]},
        },
        "env": env,
        "resources": {
            "requests": {"cpu": "10m", "memory": "16Mi"},
            "limits": {"cpu": "50m", "memory": "32Mi"},
        },
    }


def _throughput_stats_container(
    a_name: str,
    sid: int,
    profile_name: str,
    app_type: str,
    cluster: str,
) -> dict:
    """UE sidecar: oaitun RX/TX Mbps via dedicated throughput-statistics image."""
    env = _ue_influx_env(a_name, sid, profile_name, app_type, cluster)
    env.extend(
        [
            {"name": "IFACE", "value": ""},
            {"name": "IFACE_PREFIX", "value": "oaitun"},
            {"name": "SAMPLE_INTERVAL_S", "value": "1"},
        ]
    )
    return {
        "name": "throughput-statistics",
        "image": app_images.THROUGHPUT_STATS,
        "imagePullPolicy": "Always",
        "env": env,
        "resources": {
            "requests": {"cpu": "10m", "memory": "16Mi"},
            "limits": {"cpu": "50m", "memory": "32Mi"},
        },
    }


CONTROL_DASH_IMAGE = "10.1.132.30:5000/ina-control-dashboard:nws-v0.21-amd64"
APP_BACKEND_CONTAINER = "application-backend"
APP_CONSOLE_CONTAINER = "application-console"


def _control_dashboard_container(
    *,
    app_kind: str,
    app_name: str,
    target_container: str,
    dash_port: int,
    metrics_port: int,
    extra_env: Optional[list] = None,
) -> dict:
    env = [
        {"name": "POD_NAMESPACE", "valueFrom": {"fieldRef": {"fieldPath": "metadata.namespace"}}},
        {"name": "APP_KIND", "value": app_kind},
        {"name": "DEPLOY_NAME", "value": app_name},
        {"name": "TARGET_CONTAINER", "value": target_container},
        {"name": "METRICS_URL", "value": f"http://127.0.0.1:{metrics_port}/metrics"},
        {"name": "DASHBOARD_PORT", "value": str(dash_port)},
    ]
    if extra_env:
        env.extend(extra_env)
    return {
        "name": APP_CONSOLE_CONTAINER,
        "image": CONTROL_DASH_IMAGE,
        "imagePullPolicy": "Always",
        "command": ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(dash_port)],
        "env": env,
        "ports": [{"name": "dashboard", "containerPort": dash_port}],
        "resources": {
            "requests": {"cpu": "20m", "memory": "64Mi"},
            "limits": {"cpu": "200m", "memory": "128Mi"},
        },
    }


def _control_dashboard_rbac(app_name: str, profile_name: str, labels: dict) -> list:
    sa = {
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {"name": app_name, "namespace": profile_name, "labels": labels},
    }
    role = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {"name": f"{app_name}-dashboard", "namespace": profile_name, "labels": labels},
        "rules": [
            {
                "apiGroups": ["apps"],
                "resources": ["deployments"],
                "resourceNames": [app_name],
                "verbs": ["get", "patch"],
            },
        ],
    }
    rb = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "RoleBinding",
        "metadata": {"name": f"{app_name}-dashboard", "namespace": profile_name, "labels": labels},
        "subjects": [{"kind": "ServiceAccount", "name": app_name, "namespace": profile_name}],
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": f"{app_name}-dashboard",
        },
    }
    return [sa, role, rb]


def generate_server_manifests(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    target_cluster: str,
    profile_subnet: Optional[str] = None,
) -> List[dict]:
    """Generate Kubernetes manifests for the application Server/Analyzer/Broker on target cluster (N6 Data Network)."""
    sid = app_cfg.slice_id
    app_type = app_cfg.app_type.lower()
    p = app_cfg.params or {}

    labels = {
        "app.kubernetes.io/name": f"slice{sid}-{app_type}",
        "app.kubernetes.io/part-of": profile_name,
        "ina.lab/slice": str(sid),
        "ina.lab/app-type": app_type,
        "ina.lab/role": "server",
    }

    # Static N6 / console IP outside Glass DHCP .160–.199 (slice 1 → .211).
    app_multus_ip = site_ips.application_multus_ip(sid)
    gw = site_ips.SITE_GW

    if app_type == "physical_ai":
        gpu_arch = _physical_ai_gpu_arch(target_cluster.lower(), p)
        master = multus_iface.detect_gpu_worker_master(
            target_cluster.lower(), arch=gpu_arch
        )
    else:
        master = multus_iface.detect_cluster_master(target_cluster.lower())

    nad_name = f"app-slice{sid}-multus"
    nad_manifest = {
        "apiVersion": "k8s.cni.cncf.io/v1",
        "kind": "NetworkAttachmentDefinition",
        "metadata": {
            "name": nad_name,
            "namespace": profile_name,
            "labels": {
                "app.kubernetes.io/name": nad_name,
                "app.kubernetes.io/part-of": profile_name,
                "ina-infra.nephio.lab/role": "app",
                "ina.lab/slice": str(sid),
            },
        },
        "spec": {
            "config": json.dumps({
                "cniVersion": "0.3.1",
                "name": nad_name,
                "plugins": [
                    {
                        "type": "macvlan",
                        "capabilities": {"ips": True, "mac": True},
                        "master": master,
                        "mode": "bridge",
                        "ipam": {
                            "type": "static",
                            "addresses": [
                                {"address": f"{app_multus_ip}/24", "gateway": gw}
                            ],
                        },
                    },
                    {
                        "type": "tuning",
                        "capabilities": {"mac": True},
                        "ipam": {},
                        "sysctl": {
                            "net.ipv4.conf.IFNAME.arp_ignore": "1",
                            "net.ipv4.conf.IFNAME.arp_announce": "2",
                        },
                    },
                ],
            }),
        },
    }

    app_mac = site_ips.application_multus_mac(sid)
    net_annot = json.dumps([
        {
            "name": nad_name,
            "interface": "net1",
            "ips": [f"{app_multus_ip}/24"],
            "gateways": [gw],
            "mac": app_mac,
        }
    ])

    manifests: List[dict] = [nad_manifest]

    common_env = [
        {"name": "MULTUS_IP", "value": app_multus_ip},
        {"name": "MULTUS_SUBNET", "value": "10.1.137.0/24"},
        {"name": "MULTUS_GATEWAY", "value": gw},
        {"name": "INFLUXDB_URL", "value": "http://10.1.137.104:8086"},
        {"name": "INFLUXDB_TOKEN", "value": "ina-infra-influxdb-token"},
        {"name": "INFLUXDB_ORG", "value": "ina-infra"},
        {"name": "INFLUXDB_BUCKET", "value": "default"},
        {"name": "INFLUXDB_MEASUREMENT", "value": "application_metrics"},
        {"name": "INFLUX_URL", "value": "http://10.1.137.104:8086"},
        {"name": "INFLUX_TOKEN", "value": "ina-infra-influxdb-token"},
        {"name": "INFLUX_ORG", "value": "ina-infra"},
        {"name": "INFLUX_BUCKET", "value": "default"},
        {"name": "INFLUX_MEASUREMENT", "value": "application_metrics"},
        {"name": "SLICE_ID", "value": str(sid)},
        {"name": "PROFILE_NAME", "value": profile_name},
        {"name": "APP_TYPE", "value": app_type},
        {"name": "TARGET_CLUSTER", "value": target_cluster},
    ]

    if app_type == "cctv":
        app_name = "application-cctv"
        labels["app.kubernetes.io/name"] = app_name
        rtsp_port = int(p.get("rtsp_port") or app_cfg.server_port or 8554)
        http_port = int(p.get("http_port") or 8080)
        metrics_port = int(p.get("metrics_port") or app_cfg.metrics_port or 9102)
        stream_path = str(p.get("stream_path") or "slicea")
        yolo_model = str(p.get("yolo_model") or "yolov8n.pt")
        yolo_device = str(p.get("yolo_device") or "auto")
        frame_skip = str(p.get("frame_skip") or 1)

        server_img = app_images.resolve_server_image("cctv", app_cfg.server_image)

        def _read_edge(name: str) -> str:
            path = f"/home/fcp/INA-Infra/applications/server/cctv/edge/{name}"
            if not os.path.exists(path):
                path = f"/home/fcp/INA-Infra/applications/cctv/edge/{name}"
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            return ""

        cm_data = {
            "cctv.py": _read_edge("cctv.py"),
            "yolo_worker.py": _read_edge("yolo_worker.py"),
            "state.py": _read_edge("state.py"),
            "api.py": _read_edge("api.py"),
            "mtx_publish.py": _read_edge("mtx_publish.py"),
            "mediamtx.yml": _read_edge("mediamtx.yml"),
            "entrypoint.sh": _read_edge("entrypoint.sh"),
            "__init__.py": _read_edge("__init__.py"),
        }

        cm_manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"{app_name}-code",
                "namespace": profile_name,
                "labels": labels,
            },
            "data": cm_data,
        }

        edge_mounts = [
            {"name": "code-volume", "mountPath": f"/app/edge/{fn}", "subPath": fn}
            for fn in cm_data
        ]

        pod_spec: dict = {
            "initContainers": [site_ips.multus_src_route_init(app_multus_ip, "net1")],
            "nodeSelector": multus_iface.scheduling_node_selector("amd64", master),
            "hostPID": True,
            "volumes": [
                {
                    "name": "code-volume",
                    "configMap": {
                        "name": f"{app_name}-code",
                        "defaultMode": 0o755
                    }
                }
            ],
            "containers": [
                {
                    "name": APP_BACKEND_CONTAINER,
                    "image": server_img,
                    "imagePullPolicy": "Always",
                    "volumeMounts": edge_mounts,
                    "env": [
                        *common_env,
                        {"name": "APP_NAME", "value": app_name},
                        {"name": "BIND_ADDRESS", "value": "0.0.0.0"},
                        {"name": "RTSP_PORT", "value": str(rtsp_port)},
                        {"name": "HTTP_PORT", "value": str(http_port)},
                        {"name": "STREAM_PATH", "value": stream_path},
                        {"name": "RTSP_LATENCY_MS", "value": "0"},
                        {"name": "YOLO_ENABLED", "value": "true"},
                        {"name": "YOLO_MODEL", "value": yolo_model},
                        {"name": "YOLO_DEVICE", "value": yolo_device},
                        {"name": "YOLO_PROCESS_PER_CLIENT", "value": "true"},
                        {"name": "PYTHONPATH", "value": "/app"},
                        {"name": "FRONTEND_DIR", "value": "/app/frontend/dist"},
                        {"name": "FRAME_SKIP", "value": str(frame_skip)},
                        {"name": "METRICS_PORT", "value": str(metrics_port)},
                        {"name": "METRICS_ADDR", "value": "0.0.0.0"},
                        {"name": "MTX_RTSP_URL", "value": "rtsp://127.0.0.1:8555"},
                        {"name": "MTX_HLS_URL", "value": "http://127.0.0.1:8888"},
                        {"name": "MTX_WHEP_URL", "value": "http://127.0.0.1:8889"},
                        {"name": "MTX_API_URL", "value": "http://127.0.0.1:9997"},
                        {"name": "MTX_PUBLISH_BITRATE_KBPS", "value": "12000"},
                        {"name": "MTX_X264_PRESET", "value": "veryfast"},
                        {"name": "MTX_X264_PROFILE", "value": "high"},
                        {"name": "START_MEDIAMTX", "value": "false"},
                        {"name": "LOG_INTERVAL_S", "value": "1"},
                    ],
                    "ports": [
                        {"name": "rtsp", "containerPort": rtsp_port},
                        {"name": "http", "containerPort": http_port},
                        {"name": "metrics", "containerPort": metrics_port},
                    ],
                    "resources": {
                        "requests": {"cpu": "500m", "memory": "1Gi"},
                    },
                },
                # Container 2: Dedicated MediaMTX Sidecar
                {
                    "name": "mediamtx",
                    "image": "docker.io/bluenviron/mediamtx:1.12.2",
                    "imagePullPolicy": "Always",
                    "args": ["/app/edge/mediamtx.yml"],
                    "ports": [
                        {"name": "rtsp-publish", "containerPort": 8555},
                        {"name": "hls", "containerPort": 8888},
                        {"name": "webrtc", "containerPort": 8889},
                        {"name": "api", "containerPort": 9997},
                    ],
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "1000m", "memory": "512Mi"},
                    },
                    "volumeMounts": [
                        {"name": "code-volume", "mountPath": "/app/edge/mediamtx.yml", "subPath": "mediamtx.yml"}
                    ],
                },
                # Container 3: Dedicated Nginx + React Video Wall Frontend Sidecar
                {
                    "name": APP_CONSOLE_CONTAINER,
                    "image": "10.1.132.30:5000/application-cctv-frontend:nws-v0.15-amd64",
                    "imagePullPolicy": "Always",
                    "ports": [{"name": "web", "containerPort": 80}],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "500m", "memory": "256Mi"},
                    },
                },
                _influx_pusher_container(metrics_port, app_name, sid, profile_name, app_type, target_cluster),
            ]
        }

        deploy_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {
                            "k8s.v1.cni.cncf.io/networks": net_annot,
                        },
                    },
                    "spec": pod_spec,
                },
            },
        }

        svc_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "type": "ClusterIP",
                "selector": labels,
                "ports": [
                    {"name": "web", "port": 80, "targetPort": 80},
                    {"name": "rtsp", "port": rtsp_port, "targetPort": rtsp_port},
                    {"name": "http", "port": http_port, "targetPort": http_port},
                    {"name": "metrics", "port": metrics_port, "targetPort": metrics_port},
                ],
            },
        }

        manifests.extend([cm_manifest, deploy_manifest, svc_manifest])

    elif app_type == "physical_ai":
        app_name = "application-physical-ai"
        labels["app.kubernetes.io/name"] = app_name
        http_port = int(p.get("server_port") or app_cfg.server_port or 8000)
        metrics_port = int(p.get("metrics_port") or app_cfg.metrics_port or 8002)
        model_name = _physical_ai_model(p)
        max_len = str(p.get("max_model_len") or "4096")

        gpu_arch = _physical_ai_gpu_arch(target_cluster.lower(), p)
        server_img = _physical_ai_server_image(app_cfg.server_image, gpu_arch)
        gpu_mem = str(p.get("gpu_memory_utilization") or ("0.75" if gpu_arch == "amd64" else "0.6"))
        gpu_worker = multus_iface.pick_gpu_worker(target_cluster.lower(), arch=gpu_arch)
        node_sel = dict(multus_iface.scheduling_node_selector(gpu_arch, master))
        if gpu_worker and gpu_worker.get("name"):
            node_sel["kubernetes.io/hostname"] = gpu_worker["name"]
        elif target_cluster.lower() == "edge":
            node_sel["kubernetes.io/hostname"] = "gpu-a40"
            node_sel["kubernetes.io/arch"] = "amd64"

        dash_img = str(
            p.get("dashboard_image")
            or "10.1.132.30:5000/cosmo3-dashboard:nws-v0.11-amd64"
        )
        dash_port = int(p.get("dashboard_port") or site_ips.CONSOLE_PORT)
        latency_proxy_port = int(p.get("latency_proxy_port") or 18080)

        def _read_dash(name: str) -> str:
            path = f"/home/fcp/INA-Infra/applications/server/physical_ai/dashboard/{name}"
            if not os.path.exists(path):
                path = f"/home/fcp/INA-Infra/applications/physical_ai/dashboard/{name}"
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            return ""

        dash_cm_data = {
            "app.py": _read_dash("app.py"),
            "index.html": _read_dash("static/index.html"),
        }
        if any(dash_cm_data.values()):
            dash_cm_manifest = {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": "physical-ai-dashboard-code",
                    "namespace": profile_name,
                    "labels": labels,
                },
                "data": {k: v for k, v in dash_cm_data.items() if v},
            }
            manifests.append(dash_cm_manifest)

        route_init = site_ips.multus_src_route_init(app_multus_ip, "net1")
        route_init["command"][2] = (
            str(route_init["command"][2]).rstrip()
            + "\n"
            + f"iptables -t nat -C PREROUTING -i net1 -p tcp --dport {http_port} "
            f"-j REDIRECT --to-port {latency_proxy_port} 2>/dev/null || "
            f"iptables -t nat -A PREROUTING -i net1 -p tcp --dport {http_port} "
            f"-j REDIRECT --to-port {latency_proxy_port} || true\n"
            "iptables -t nat -L PREROUTING -n || true\n"
        )
        pod_spec = {
            "initContainers": [route_init],
            "serviceAccountName": app_name,
            "automountServiceAccountToken": True,
            "nodeSelector": node_sel,
            "runtimeClassName": "nvidia",
            "volumes": [
                {
                    "name": "hf-token-vol",
                    "secret": {
                        "secretName": "ina-hf-token",
                        "optional": True,
                    },
                },
                {
                    "name": "dashboard-code",
                    "configMap": {
                        "name": "physical-ai-dashboard-code",
                        "defaultMode": 0o644,
                    },
                },
                {
                    "name": "dashboard-static",
                    "emptyDir": {},
                },
            ],
            "containers": [
                {
                    "name": APP_BACKEND_CONTAINER,
                    "image": server_img,
                    "imagePullPolicy": "Always",
                    "command": [
                        "/bin/bash",
                        "-c",
                        (
                            "TOKEN_FILE=/var/run/secrets/hf-token/token\n"
                            "echo \"[vllm-wrapper] Waiting for HF token at ${TOKEN_FILE}...\"\n"
                            "while true; do\n"
                            "  if [ -f \"${TOKEN_FILE}\" ] && [ -s \"${TOKEN_FILE}\" ]; then\n"
                            "    echo \"[vllm-wrapper] Token found, starting vLLM.\"\n"
                            "    break\n"
                            "  fi\n"
                            "  echo \"[vllm-wrapper] Token not ready, retrying in 10s. Enter via console at :80\"\n"
                            "  sleep 10\n"
                            "done\n"
                            "export HF_TOKEN=$(cat \"${TOKEN_FILE}\")\n"
                            "export HUGGING_FACE_HUB_TOKEN=\"${HF_TOKEN}\"\n"
                            "exec /app/entrypoint-vllm.sh\n"
                        ),
                    ],
                    "env": [
                        *common_env,
                        {"name": "APP_NAME", "value": app_name},
                        {"name": "MODEL_NAME", "value": model_name},
                        {"name": "MODEL", "value": model_name},
                        {"name": "PORT", "value": str(http_port)},
                        {"name": "HOST_PORT", "value": str(http_port)},
                        {"name": "MAX_MODEL_LEN", "value": max_len},
                        {"name": "GPU_MEMORY_UTILIZATION", "value": gpu_mem},
                        {"name": "METRICS_PORT", "value": str(metrics_port)},
                        {
                            "name": "HF_TOKEN",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": "ina-hf-token",
                                    "key": "token",
                                    "optional": True,
                                }
                            },
                        },
                        {
                            "name": "HUGGING_FACE_HUB_TOKEN",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": "ina-hf-token",
                                    "key": "token",
                                    "optional": True,
                                }
                            },
                        },
                    ],
                    "volumeMounts": [
                        {
                            "name": "hf-token-vol",
                            "mountPath": "/var/run/secrets/hf-token",
                            "readOnly": True,
                        },
                    ],
                    "ports": [
                        {"name": "http", "containerPort": http_port},
                        {"name": "metrics", "containerPort": metrics_port},
                    ],
                    "resources": {
                        "requests": {"cpu": "2", "memory": "8Gi", "nvidia.com/gpu": "1"},
                        "limits": {"cpu": "16", "memory": "32Gi", "nvidia.com/gpu": "1"},
                    },
                },
                {
                    "name": APP_CONSOLE_CONTAINER,
                    "image": dash_img,
                    "imagePullPolicy": "Always",
                    "command": ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", str(dash_port)],
                    "env": [
                        {"name": "POD_NAMESPACE", "valueFrom": {"fieldRef": {"fieldPath": "metadata.namespace"}}},
                        {"name": "MODEL_NAME", "value": model_name},
                        {"name": "VLLM_URL", "value": f"http://127.0.0.1:{http_port}"},
                        {"name": "LATENCY_PROXY_PORT", "value": str(latency_proxy_port)},
                        {"name": "HF_SECRET_NAME", "value": "ina-hf-token"},
                        {"name": "HF_DEPLOY_NAME", "value": app_name},
                        {"name": "DASHBOARD_STATIC", "value": "/app/static"},
                        {"name": "SLICE_ID", "value": str(sid)},
                        {"name": "DASHBOARD_PORT", "value": str(dash_port)},
                        {"name": "UE_CONSOLE_PORT", "value": str(PHYSICAL_AI_CONSOLE_PORT)},
                    ],
                    "volumeMounts": [
                        {
                            "name": "dashboard-code",
                            "mountPath": "/app/app.py",
                            "subPath": "app.py",
                        },
                        {
                            "name": "dashboard-code",
                            "mountPath": "/app/static/index.html",
                            "subPath": "index.html",
                        },
                    ],
                    "ports": [
                        {"name": "dashboard", "containerPort": dash_port},
                    ],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "500m", "memory": "256Mi"},
                    },
                },
                _influx_pusher_container(
                    metrics_port,
                    app_name,
                    sid,
                    profile_name,
                    app_type,
                    target_cluster,
                    extra_env=[{"name": "APP_METRICS_URL", "value": f"http://127.0.0.1:{dash_port}/metrics"}],
                ),
            ],
        }

        deploy_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {
                            "k8s.v1.cni.cncf.io/networks": net_annot,
                        },
                    },
                    "spec": pod_spec,
                },
            },
        }

        svc_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "type": "ClusterIP",
                "selector": labels,
                "ports": [
                    {"name": "http", "port": http_port, "targetPort": http_port},
                    {"name": "dashboard", "port": dash_port, "targetPort": dash_port},
                    {"name": "metrics", "port": metrics_port, "targetPort": metrics_port},
                ],
            },
        }
        sa_manifest = {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": app_name, "namespace": profile_name, "labels": labels},
        }
        role_manifest = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {"name": f"{app_name}-dashboard", "namespace": profile_name, "labels": labels},
            "rules": [
                {"apiGroups": [""], "resources": ["secrets"], "verbs": ["create"]},
                {
                    "apiGroups": [""],
                    "resources": ["secrets"],
                    "resourceNames": ["ina-hf-token"],
                    "verbs": ["get", "update", "patch", "delete"],
                },
                {
                    "apiGroups": ["apps"],
                    "resources": ["deployments"],
                    "verbs": ["get", "list", "patch"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list"],
                },
            ],
        }
        rb_manifest = {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": f"{app_name}-dashboard", "namespace": profile_name, "labels": labels},
            "subjects": [{"kind": "ServiceAccount", "name": app_name, "namespace": profile_name}],
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": f"{app_name}-dashboard",
            },
        }
        manifests.extend([sa_manifest, role_manifest, rb_manifest, deploy_manifest, svc_manifest])

    elif app_type == "ott":
        app_name = "application-ott"
        labels["app.kubernetes.io/name"] = app_name
        rtsp_port = int(p.get("rtsp_port") or app_cfg.server_port or 8554)
        http_port = int(p.get("http_port") or 8080)
        metrics_port = int(p.get("metrics_port") or app_cfg.metrics_port or 9103)
        stream_path = str(p.get("stream_path") or "live/hd")
        bitrate = str(p.get("bitrate_kbps") or "6000")
        fps = str(p.get("fps") or "25")

        server_img = app_images.resolve_server_image("ott", app_cfg.server_image)

        def _read_ott(name: str) -> str:
            path = f"/home/fcp/INA-Infra/applications/server/ott/server/{name}"
            if not os.path.exists(path):
                path = f"/home/fcp/INA-Infra/applications/ott/server/{name}"
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            return ""

        cm_data = {
            "main.py": _read_ott("main.py"),
            "ott.py": _read_ott("ott.py"),
            "api.py": _read_ott("api.py"),
            "state.py": _read_ott("state.py"),
            "youtube_resolver.py": _read_ott("youtube_resolver.py"),
            "mediamtx.yml": _read_ott("mediamtx.yml"),
            "entrypoint.sh": _read_ott("entrypoint.sh"),
            "__init__.py": _read_ott("__init__.py"),
        }

        cm_manifest = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": f"{app_name}-code",
                "namespace": profile_name,
                "labels": labels,
            },
            "data": {k: v for k, v in cm_data.items() if v},
        }

        ott_mounts = [
            {"name": "code-volume", "mountPath": f"/app/server/{fn}", "subPath": fn}
            for fn, v in cm_data.items() if v
        ]

        pod_spec = {
            "initContainers": [site_ips.multus_src_route_init(app_multus_ip, "net1")],
            "serviceAccountName": app_name,
            "automountServiceAccountToken": True,
            "nodeSelector": multus_iface.scheduling_node_selector("amd64", master),
            "volumes": [
                {
                    "name": "code-volume",
                    "configMap": {
                        "name": f"{app_name}-code",
                        "defaultMode": 0o755,
                    },
                },
            ],
            "containers": [
                {
                    "name": APP_BACKEND_CONTAINER,
                    "image": server_img,
                    "imagePullPolicy": "Always",
                    "volumeMounts": ott_mounts,
                    "env": [
                        *common_env,
                        {"name": "APP_NAME", "value": app_name},
                        {"name": "RTSP_PORT", "value": str(rtsp_port)},
                        {"name": "HTTP_PORT", "value": str(http_port)},
                        {"name": "STREAM_PATH", "value": stream_path},
                        {"name": "METRICS_PORT", "value": str(metrics_port)},
                        {"name": "BITRATE_KBPS", "value": bitrate},
                        {"name": "FPS", "value": fps},
                        {"name": "MTX_RTSP_URL", "value": "rtsp://127.0.0.1:8555"},
                        {"name": "MTX_HLS_URL", "value": "http://127.0.0.1:8888"},
                        {"name": "MTX_WHEP_URL", "value": "http://127.0.0.1:8889"},
                        {"name": "MTX_API_URL", "value": "http://127.0.0.1:9997"},
                        {"name": "START_MEDIAMTX", "value": "false"},
                        # Direct googlevideo CDN often 403s; yt-dlp caches to disk instead.
                        {"name": "OTT_FORCE_TESTSRC", "value": "0"},
                        {"name": "OTT_YOUTUBE_CACHE", "value": "/tmp/ott-youtube"},
                        {"name": "OTT_YOUTUBE_MAX_HEIGHT", "value": "720"},
                        # UE/browser plays YouTube directly (no MediaMTX republish).
                        {"name": "OTT_PLAY_MODE", "value": "youtube"},
                    ],
                    "ports": [
                        {"name": "rtsp", "containerPort": rtsp_port},
                        {"name": "http", "containerPort": http_port},
                        {"name": "metrics", "containerPort": metrics_port},
                    ],
                    "resources": {
                        "requests": {"cpu": "200m", "memory": "256Mi"},
                        "limits": {"cpu": "2", "memory": "2Gi"},
                    },
                },
                # Container 2: Dedicated MediaMTX Sidecar
                {
                    "name": "mediamtx",
                    "image": "docker.io/bluenviron/mediamtx:1.12.2",
                    "imagePullPolicy": "Always",
                    "args": ["/app/server/mediamtx.yml"],
                    "ports": [
                        {"name": "rtsp-publish", "containerPort": 8555},
                        {"name": "hls", "containerPort": 8888},
                        {"name": "webrtc", "containerPort": 8889},
                        {"name": "api", "containerPort": 9997},
                    ],
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "1000m", "memory": "512Mi"},
                    },
                    "volumeMounts": [
                        {"name": "code-volume", "mountPath": "/app/server/mediamtx.yml", "subPath": "mediamtx.yml"}
                    ],
                },
                # Container 3: Dedicated Nginx + React OTT Portal Frontend
                {
                    "name": APP_CONSOLE_CONTAINER,
                    "image": "10.1.132.30:5000/application-ott-frontend:nws-v0.16-amd64",
                    "imagePullPolicy": "Always",
                    "ports": [{"name": "web", "containerPort": 80}],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "500m", "memory": "256Mi"},
                    },
                },
                _influx_pusher_container(metrics_port, app_name, sid, profile_name, app_type, target_cluster),
            ],
        }

        deploy_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {
                            "k8s.v1.cni.cncf.io/networks": net_annot,
                        },
                    },
                    "spec": pod_spec,
                },
            },
        }

        svc_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "type": "ClusterIP",
                "selector": labels,
                "ports": [
                    {"name": "web", "port": 80, "targetPort": 80},
                    {"name": "rtsp", "port": rtsp_port, "targetPort": rtsp_port},
                    {"name": "http", "port": http_port, "targetPort": http_port},
                    {"name": "metrics", "port": metrics_port, "targetPort": metrics_port},
                ],
            },
        }
        manifests.extend(
            [
                {
                    "apiVersion": "v1",
                    "kind": "ServiceAccount",
                    "metadata": {
                        "name": app_name,
                        "namespace": profile_name,
                        "labels": labels,
                    },
                },
                cm_manifest,
                deploy_manifest,
                svc_manifest,
            ]
        )

    elif app_type == "iot":
        app_name = "application-iot"
        labels["app.kubernetes.io/name"] = app_name
        broker_port = int(p.get("broker_port") or app_cfg.server_port or 1883)
        metrics_port = int(p.get("metrics_port") or app_cfg.metrics_port or 9105)
        dash_port = int(p.get("dashboard_port") or site_ips.CONSOLE_PORT)
        dl_fast = str(p.get("dl_fast_period_s") or "300")
        dl_slow = str(p.get("dl_slow_period_s") or "3600")
        mqtt_qos = str(p.get("mqtt_qos") or "0")

        server_img = app_images.resolve_server_image("iot", app_cfg.server_image)
        mosq_img = str(p.get("mosquitto_image") or IOT_MOSQUITTO_IMAGE).strip() or IOT_MOSQUITTO_IMAGE

        pod_spec = {
            "initContainers": [site_ips.multus_src_route_init(app_multus_ip, "net1")],
            "serviceAccountName": app_name,
            "automountServiceAccountToken": True,
            "nodeSelector": multus_iface.scheduling_node_selector("amd64", master),
            "containers": [
                {
                    "name": "mosquitto",
                    "image": mosq_img,
                    "imagePullPolicy": "Always",
                    "ports": [
                        {"name": "mqtt", "containerPort": broker_port},
                        {"name": "mqtt-ctl", "containerPort": IOT_MOSQUITTO_CONTROL_PORT},
                    ],
                    "readinessProbe": {
                        "tcpSocket": {"port": broker_port},
                        "initialDelaySeconds": 2,
                        "periodSeconds": 5,
                    },
                    "resources": {
                        "requests": {"cpu": "20m", "memory": "32Mi"},
                        "limits": {"cpu": "200m", "memory": "128Mi"},
                    },
                },
                {
                    "name": APP_BACKEND_CONTAINER,
                    "image": server_img,
                    "imagePullPolicy": "Always",
                    "env": [
                        *common_env,
                        {"name": "APP_NAME", "value": app_name},
                        {"name": "OTA_BIND_IP", "value": "0.0.0.0"},
                        {"name": "METRICS_BIND_IP", "value": "0.0.0.0"},
                        {"name": "METRICS_PORT", "value": str(metrics_port)},
                        {"name": "LOCAL_BROKER_HOST", "value": "127.0.0.1"},
                        {"name": "LOCAL_BROKER_PORT", "value": str(IOT_MOSQUITTO_CONTROL_PORT)},
                        {"name": "DL_FAST_PERIOD_S", "value": dl_fast},
                        {"name": "DL_SLOW_PERIOD_S", "value": dl_slow},
                        {"name": "DL_PAYLOAD_BYTES", "value": "256"},
                        {"name": "DEVICE_TTL_S", "value": "7200"},
                        {"name": "MQTT_QOS", "value": mqtt_qos},
                        {"name": "LOG_INTERVAL_S", "value": "30"},
                        {"name": "LOG_LEVEL", "value": "INFO"},
                    ],
                    "ports": [
                        {"name": "metrics", "containerPort": metrics_port},
                    ],
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "1", "memory": "512Mi"},
                    },
                },
                _control_dashboard_container(
                    app_kind="iot",
                    app_name=app_name,
                    target_container=APP_BACKEND_CONTAINER,
                    dash_port=dash_port,
                    metrics_port=metrics_port,
                    extra_env=[
                        {"name": "BROKER_PORT", "value": str(broker_port)},
                        {"name": "MQTT_HOST", "value": "127.0.0.1"},
                        {"name": "MQTT_PORT", "value": "1884"},
                        {"name": "DL_FAST_PERIOD_S", "value": dl_fast},
                        {"name": "DL_SLOW_PERIOD_S", "value": dl_slow},
                        {"name": "DL_PAYLOAD_BYTES", "value": "256"},
                        {"name": "MQTT_QOS", "value": mqtt_qos},
                        {"name": "MULTUS_IP", "value": app_multus_ip},
                        {"name": "SLICE_ID", "value": str(sid)},
                        {"name": "UE_CONSOLE_PORT", "value": str(site_ips.UE_CONSOLE_PORT)},
                        {"name": "UE_SCAN_MAX", "value": str(p.get("ue_scan_max") or 8)},
                    ],
                ),
                _influx_pusher_container(metrics_port, app_name, sid, profile_name, app_type, target_cluster),
            ]
        }

        deploy_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate", "rollingUpdate": None},
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {
                            "k8s.v1.cni.cncf.io/networks": net_annot,
                        },
                    },
                    "spec": pod_spec,
                },
            },
        }

        svc_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "type": "ClusterIP",
                "selector": labels,
                "ports": [
                    {"name": "mqtt", "port": broker_port, "targetPort": broker_port},
                    {"name": "dashboard", "port": dash_port, "targetPort": dash_port},
                    {"name": "metrics", "port": metrics_port, "targetPort": metrics_port},
                ],
            },
        }
        manifests.extend(_control_dashboard_rbac(app_name, profile_name, labels))
        manifests.extend([deploy_manifest, svc_manifest])

    elif app_type == "custom":
        app_name = "application-custom"
        labels["app.kubernetes.io/name"] = app_name
        port = int(app_cfg.server_port or 8080)
        metrics_port = int(app_cfg.metrics_port or 9100)
        server_img = app_cfg.server_image or "nginx:alpine"

        deploy_manifest = {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": labels},
                "template": {
                    "metadata": {
                        "labels": labels,
                        "annotations": {
                            "k8s.v1.cni.cncf.io/networks": net_annot,
                        },
                    },
                    "spec": {
                        "nodeSelector": multus_iface.scheduling_node_selector("amd64", master),
                        "containers": [
                            {
                                "name": "app",
                                "image": server_img,
                                "imagePullPolicy": "Always",
                                "env": [*common_env, {"name": "APP_NAME", "value": app_name}],
                                "ports": [{"containerPort": port}],
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "128Mi"},
                                    "limits": {"cpu": "1", "memory": "1Gi"},
                                },
                            },
                            _influx_pusher_container(metrics_port, app_name, sid, profile_name, app_type, target_cluster),
                        ]
                    },
                },
            },
        }
        svc_manifest = {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {
                "name": app_name,
                "namespace": profile_name,
                "labels": labels,
            },
            "spec": {
                "type": "ClusterIP",
                "selector": labels,
                "ports": [{"port": port, "targetPort": port}],
            },
        }
        manifests.extend([deploy_manifest, svc_manifest])

    return manifests


def build_client_container(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    server_ip: Optional[str] = None,
    client_index: int = 1,
) -> Optional[dict]:
    """Build client application sidecar container specification for running inside the oai-ue-{sid} pod."""
    sid = app_cfg.slice_id
    app_type = app_cfg.app_type.lower()
    p = app_cfg.params or {}

    if not server_ip:
        server_ip = site_ips.application_multus_ip(sid)

    common_env = [
        {"name": "TARGET_SERVER_IP", "value": server_ip},
        {"name": "PDU_IFACE", "value": f"oaitun_ue{sid}"},
        {"name": "PDU_ROUTE_HOSTS", "value": server_ip},
        {"name": "PDU_WAIT_TIMEOUT", "value": "300"},
        {"name": "INFLUXDB_URL", "value": "http://10.1.137.104:8086"},
        {"name": "INFLUXDB_TOKEN", "value": "ina-infra-influxdb-token"},
        {"name": "INFLUXDB_ORG", "value": "ina-infra"},
        {"name": "INFLUXDB_BUCKET", "value": "default"},
        {"name": "INFLUXDB_MEASUREMENT", "value": "application_metrics"},
        {"name": "SLICE_ID", "value": str(sid)},
        {"name": "PROFILE_NAME", "value": profile_name},
        {"name": "APP_TYPE", "value": app_type},
        {"name": "TARGET_CLUSTER", "value": "edge"},
    ]

    client_name = f"app-client-{app_type}"
    client_m_port = 9101

    if app_type == "cctv":
        client_name = "cctv-publisher"
        rtsp_port = int(p.get("rtsp_port") or app_cfg.server_port or 8554)
        raw_stream_path = str(p.get("stream_path") or f"cctv/ue{sid}").strip()
        base_stream_path = re.sub(r"(_cam\d+)+$", "", raw_stream_path)
        stream_path = (
            base_stream_path
            if client_index <= 1
            else f"{base_stream_path}_cam{client_index}"
        )
        client_m_port = int(p.get("client_metrics_port") or (9100 + client_index))
        video_src, video_url, _video_label = cctv_videos.clip_for_client(
            p, client_index
        )
        client_img = app_images.resolve_client_image("cctv", app_cfg.client_image)
        app_name = f"slice{sid}-cctv-client-{client_index}"
        return {
            "name": client_name,
            "image": client_img,
            "imagePullPolicy": "Always",
            "securityContext": {
                "privileged": True,
                "capabilities": {"add": ["NET_ADMIN"]},
            },
            "volumeMounts": [
                {
                    "name": "cctv-client-code",
                    "mountPath": "/app/client/publisher.py",
                    "subPath": "publisher.py",
                },
                {
                    "name": "cctv-data",
                    "mountPath": "/data",
                },
            ],
            "env": [
                *common_env,
                {"name": "APP_NAME", "value": app_name},
                {"name": "VIDEO_SOURCE", "value": video_src},
                {"name": "VIDEO_URL", "value": video_url},
                {"name": "RTSP_TARGET_HOST", "value": server_ip},
                {"name": "RTSP_SERVER", "value": server_ip},
                {"name": "RTSP_PORT", "value": str(rtsp_port)},
                {"name": "STREAM_PATH", "value": stream_path},
                {"name": "FPS", "value": str(p.get("fps") or 25)},
                {"name": "BITRATE_KBPS", "value": str(p.get("bitrate_kbps") or 4000)},
                {"name": "RTSP_PROTOCOL", "value": str(p.get("rtsp_protocol") or "tcp")},
                {"name": "METRICS_PORT", "value": str(client_m_port)},
                {"name": "METRICS_ADDR", "value": "0.0.0.0"},
                {"name": "LOG_INTERVAL_S", "value": "1"},
            ],
            "ports": [{"name": "metrics", "containerPort": client_m_port}],
            "resources": {
                "requests": {"cpu": "200m", "memory": "256Mi"},
                "limits": {"cpu": "2", "memory": "2Gi"},
            },
        }

    elif app_type == "physical_ai":
        return None

    elif app_type == "ott":
        client_name = "ott-client"
        rtsp_port = int(p.get("rtsp_port") or app_cfg.server_port or 8554)
        stream_path = str(p.get("stream_path") or "live/hd")
        client_m_port = int(p.get("client_metrics_port") or 9104)
        client_img = app_images.resolve_client_image("ott", app_cfg.client_image)
        return {
            "name": client_name,
            "image": client_img,
            "imagePullPolicy": "Always",
            "securityContext": {
                "privileged": True,
                "capabilities": {"add": ["NET_ADMIN"]},
            },
            "env": [
                *common_env,
                {"name": "APP_NAME", "value": f"slice{sid}-ott-client"},
                {"name": "RTSP_SERVER", "value": server_ip},
                {"name": "RTSP_PORT", "value": str(rtsp_port)},
                {"name": "STREAM_PATH", "value": stream_path},
                {"name": "METRICS_PORT", "value": str(client_m_port)},
            ],
            "ports": [{"name": "metrics", "containerPort": client_m_port}],
            "resources": {
                "requests": {"cpu": "200m", "memory": "256Mi"},
                "limits": {"cpu": "2", "memory": "2Gi"},
            },
        }

    elif app_type == "iot":
        return None

    elif app_type == "custom" and app_cfg.client_image:
        client_name = "custom-client"
        client_m_port = int(p.get("client_metrics_port") or 9107)
        return {
            "name": client_name,
            "image": app_cfg.client_image,
            "imagePullPolicy": "Always",
            "securityContext": {
                "privileged": True,
                "capabilities": {"add": ["NET_ADMIN"]},
            },
            "env": [
                *common_env,
                {"name": "APP_NAME", "value": f"slice{sid}-custom-client"},
                {"name": "TARGET_SERVER", "value": server_ip},
                {"name": "TARGET_PORT", "value": str(app_cfg.server_port or 8080)},
                {"name": "METRICS_PORT", "value": str(client_m_port)},
            ],
            "resources": {
                "requests": {"cpu": "100m", "memory": "128Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            },
        }

    return None


def build_cctv_ue_containers(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    server_ip: Optional[str] = None,
    client_index: int = 1,
) -> List[dict]:
    """UE pod sidecars: backend (RTSP streamer over PDU) and frontend (Multus console)."""
    sid = app_cfg.slice_id
    p = app_cfg.params or {}
    idx = max(int(client_index), 1)
    if not server_ip:
        server_ip = site_ips.application_multus_ip(sid)
    rtsp_port = int(p.get("rtsp_port") or app_cfg.server_port or 8554)
    http_port = int(p.get("http_port") or 8080)
    raw_stream_path = str(p.get("stream_path") or f"cctv/ue{sid}").strip()
    base_stream_path = re.sub(r"(_cam\d+)+$", "", raw_stream_path)
    stream_path = (
        base_stream_path
        if idx <= 1
        else f"{base_stream_path}_cam{idx}"
    )
    console_ip = cctv_console_ip(idx, sid)
    console_mac = cctv_console_mac(sid, idx)
    ue_name = f"oai-ue-slice-{sid}-client-{idx}"
    raw_client = (app_cfg.client_image or "").strip()
    override = str(p.get("ue_console_image") or "").strip()
    img = app_images.resolve_client_image("cctv", override or raw_client)
    video_src, video_url, _vlabel = cctv_videos.clip_for_client(p, idx)
    client_m_port = int(p.get("client_metrics_port") or (9100 + idx))

    common = [
        {"name": "TARGET_SERVER_IP", "value": server_ip},
        {"name": "RTSP_TARGET_HOST", "value": server_ip},
        {"name": "RTSP_SERVER", "value": server_ip},
        {"name": "RTSP_PORT", "value": str(rtsp_port)},
        {"name": "HTTP_PORT", "value": str(http_port)},
        {"name": "SERVER_URL", "value": f"http://{server_ip}:{http_port}"},
        {"name": "STREAM_PATH", "value": stream_path},
        {"name": "VIDEO_SOURCE", "value": video_src},
        {"name": "VIDEO_URL", "value": video_url},
        {"name": "FPS", "value": str(p.get("fps") or 25)},
        {"name": "BITRATE_KBPS", "value": str(p.get("bitrate_kbps") or 4000)},
        {"name": "RTSP_PROTOCOL", "value": str(p.get("rtsp_protocol") or "tcp")},
        {"name": "PDU_IFACE", "value": f"oaitun_ue{sid}"},
        {"name": "PDU_ROUTE_HOSTS", "value": f"10.1.137.1,{server_ip}"},
        {"name": "PDU_WAIT_TIMEOUT", "value": "300"},
        {"name": "SLICE_ID", "value": str(sid)},
        {"name": "CLIENT_INDEX", "value": str(idx)},
        {"name": "PROFILE_NAME", "value": profile_name},
        {"name": "UE_NAME", "value": ue_name},
        {"name": "CONSOLE_IP", "value": console_ip},
        {"name": "CONSOLE_MAC", "value": console_mac},
        {"name": "BACKEND_URL", "value": f"http://127.0.0.1:{CCTV_BACKEND_PORT}"},
        {"name": "BACKEND_PORT", "value": str(CCTV_BACKEND_PORT)},
        {"name": "FRONTEND_PORT", "value": str(CCTV_CONSOLE_PORT)},
        {"name": "DASHBOARD_STATIC", "value": "/app/ue/static"},
        {"name": "STREAMING_ENABLED", "value": str(p.get("streaming_enabled", "1"))},
    ]
    resources = {
        "requests": {"cpu": "200m", "memory": "256Mi"},
        "limits": {"cpu": "2", "memory": "2Gi"},
    }
    backend = {
        "name": "backend",
        "image": img,
        "imagePullPolicy": "Always",
        "securityContext": {
            "privileged": True,
            "capabilities": {"add": ["NET_ADMIN"]},
        },
        "env": [
            *common,
            {"name": "CONSOLE_ROLE", "value": "backend"},
            {"name": "APP_NAME", "value": f"slice{sid}-cctv-client-{idx}"},
            {"name": "METRICS_PORT", "value": str(client_m_port)},
        ],
        "ports": [
            {"name": "backend", "containerPort": CCTV_BACKEND_PORT},
            {"name": "metrics", "containerPort": client_m_port},
        ],
        "resources": resources,
    }
    frontend = {
        "name": "frontend",
        "image": img,
        "imagePullPolicy": "Always",
        "env": [
            *common,
            {"name": "CONSOLE_ROLE", "value": "frontend"},
        ],
        "ports": [{"name": "console", "containerPort": CCTV_CONSOLE_PORT}],
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
    }
    return [backend, frontend, *build_ue_metrics_sidecars(profile_name, app_cfg, client_index)]


def build_physical_ai_ue_containers(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    server_ip: Optional[str] = None,
    client_index: int = 1,
) -> List[dict]:
    """UE pod sidecars: backend (PDU traffic + log) and frontend (Multus console)."""
    sid = app_cfg.slice_id
    p = app_cfg.params or {}
    idx = max(int(client_index), 1)
    if not server_ip:
        server_ip = site_ips.application_multus_ip(sid)
    http_port = int(p.get("server_port") or app_cfg.server_port or 8000)
    console_ip = physical_ai_console_ip(idx)
    console_mac = physical_ai_console_mac(sid, idx)
    ue_name = f"oai-ue-slice-{sid}-client-{idx}"
    raw_client = (app_cfg.client_image or "").strip()
    override = str(p.get("ue_console_image") or "").strip()
    img = app_images.resolve_client_image("physical_ai", override or raw_client)
    interval = str(p.get("send_interval_s") or p.get("prompt_interval_s") or 8)
    common = [
        {"name": "TARGET_SERVER_IP", "value": server_ip},
        {"name": "PDU_IFACE", "value": f"oaitun_ue{sid}"},
        {"name": "PDU_ROUTE_HOSTS", "value": server_ip},
        {"name": "PDU_WAIT_TIMEOUT", "value": "300"},
        {"name": "SLICE_ID", "value": str(sid)},
        {"name": "CLIENT_INDEX", "value": str(idx)},
        {"name": "PROFILE_NAME", "value": profile_name},
        {"name": "UE_NAME", "value": ue_name},
        {"name": "CONSOLE_IP", "value": console_ip},
        {"name": "CONSOLE_MAC", "value": console_mac},
        {"name": "SERVER_URL", "value": f"http://{server_ip}:{http_port}"},
        {"name": "URL", "value": f"http://{server_ip}:{http_port}"},
        {"name": "MODEL_NAME", "value": _physical_ai_model(p)},
        {"name": "SEND_INTERVAL_S", "value": interval},
        {"name": "SEND_ENABLED", "value": str(p.get("send_enabled", "1"))},
        {"name": "INCLUDE_IMAGE", "value": str(p.get("include_image", "1"))},
        {"name": "BACKEND_URL", "value": f"http://127.0.0.1:{PHYSICAL_AI_BACKEND_PORT}"},
        {"name": "BACKEND_PORT", "value": str(PHYSICAL_AI_BACKEND_PORT)},
        {"name": "FRONTEND_PORT", "value": str(PHYSICAL_AI_CONSOLE_PORT)},
        {"name": "DASHBOARD_STATIC", "value": "/app/static"},
    ]
    resources = {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    }
    backend = {
        "name": "backend",
        "image": img,
        "imagePullPolicy": "Always",
        "securityContext": {
            "privileged": True,
            "capabilities": {"add": ["NET_ADMIN"]},
        },
        "env": [
            *common,
            {"name": "CONSOLE_ROLE", "value": "backend"},
            {"name": "APP_NAME", "value": f"slice{sid}-physical-ai-client-{idx}"},
            {"name": "METRICS_PORT", "value": str(p.get("client_metrics_port") or 8001)},
        ],
        "ports": [
            {"name": "backend", "containerPort": PHYSICAL_AI_BACKEND_PORT},
            {"name": "metrics", "containerPort": int(p.get("client_metrics_port") or 8001)},
        ],
        "resources": resources,
    }
    frontend = {
        "name": "frontend",
        "image": img,
        "imagePullPolicy": "Always",
        "env": [
            *common,
            {"name": "CONSOLE_ROLE", "value": "frontend"},
        ],
        "ports": [{"name": "console", "containerPort": PHYSICAL_AI_CONSOLE_PORT}],
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
    }
    return [backend, frontend, *build_ue_metrics_sidecars(profile_name, app_cfg, client_index)]


def build_iot_ue_containers(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    server_ip: Optional[str] = None,
    client_index: int = 1,
) -> List[dict]:
    """UE pod sidecars: MQTT backend + Multus console for per-UE publish config."""
    sid = app_cfg.slice_id
    p = app_cfg.params or {}
    idx = max(int(client_index), 1)
    if not server_ip:
        server_ip = site_ips.application_multus_ip(sid)
    broker_port = int(p.get("broker_port") or app_cfg.server_port or 1883)
    console_ip = iot_console_ip(idx, sid)
    console_mac = iot_console_mac(sid, idx)
    ue_name = f"oai-ue-slice-{sid}-client-{idx}"
    raw_client = (app_cfg.client_image or "").strip()
    override = str(p.get("ue_console_image") or "").strip()
    img = app_images.resolve_client_image("iot", override or raw_client or IOT_UE_CONSOLE_IMAGE)
    msgs = p.get("messages") or p.get("iot_messages")
    messages_json = json.dumps(msgs) if msgs else ""
    common = [
        {"name": "TARGET_SERVER_IP", "value": server_ip},
        {"name": "PDU_IFACE", "value": f"oaitun_ue{sid}"},
        {"name": "PDU_ROUTE_HOSTS", "value": server_ip},
        {"name": "PDU_WAIT_TIMEOUT", "value": "300"},
        {"name": "SLICE_ID", "value": str(sid)},
        {"name": "CLIENT_INDEX", "value": str(idx)},
        {"name": "PROFILE_NAME", "value": profile_name},
        {"name": "UE_NAME", "value": ue_name},
        {"name": "DEVICE_ID", "value": f"ue{idx}"},
        {"name": "CONSOLE_IP", "value": console_ip},
        {"name": "CONSOLE_MAC", "value": console_mac},
        {"name": "BROKER_HOST", "value": server_ip},
        {"name": "BROKER_PORT", "value": str(broker_port)},
        {"name": "MQTT_QOS", "value": str(p.get("mqtt_qos") or 0)},
        {"name": "SEND_ENABLED", "value": str(p.get("send_enabled", "1"))},
        {"name": "BACKEND_URL", "value": f"http://127.0.0.1:{IOT_BACKEND_PORT}"},
        {"name": "BACKEND_PORT", "value": str(IOT_BACKEND_PORT)},
        {"name": "FRONTEND_PORT", "value": str(IOT_CONSOLE_PORT)},
        {"name": "DASHBOARD_STATIC", "value": "/app/static"},
    ]
    if messages_json:
        common.append({"name": "IOT_MESSAGES", "value": messages_json})
    resources = {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "1", "memory": "512Mi"},
    }
    backend = {
        "name": "backend",
        "image": img,
        "imagePullPolicy": "Always",
        "securityContext": {
            "privileged": True,
            "capabilities": {"add": ["NET_ADMIN"]},
        },
        "env": [
            *common,
            {"name": "CONSOLE_ROLE", "value": "backend"},
            {"name": "APP_NAME", "value": f"slice{sid}-iot-client-{idx}"},
            {"name": "METRICS_PORT", "value": str(p.get("client_metrics_port") or 9106)},
        ],
        "ports": [
            {"name": "backend", "containerPort": IOT_BACKEND_PORT},
            {"name": "metrics", "containerPort": int(p.get("client_metrics_port") or 9106)},
        ],
        "resources": resources,
    }
    frontend = {
        "name": "frontend",
        "image": img,
        "imagePullPolicy": "Always",
        "env": [
            *common,
            {"name": "CONSOLE_ROLE", "value": "frontend"},
        ],
        "ports": [{"name": "console", "containerPort": IOT_CONSOLE_PORT}],
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
    }
    return [backend, frontend, *build_ue_metrics_sidecars(profile_name, app_cfg, client_index)]


def build_ott_ue_volumes() -> List[dict]:
    """Shared memory + chromium config for linuxserver/chromium sidecar."""
    return [
        {"name": "chromium-config", "emptyDir": {}},
        {
            "name": "dshm",
            "emptyDir": {"medium": "Memory", "sizeLimit": "1Gi"},
        },
    ]


def build_ott_ue_containers(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    server_ip: Optional[str] = None,
    client_index: int = 1,
) -> List[dict]:
    """UE pod sidecars: backend (PDU SOCKS + CDP), frontend console, Chromium desktop."""
    sid = app_cfg.slice_id
    p = app_cfg.params or {}
    idx = max(int(client_index), 1)
    if not server_ip:
        server_ip = site_ips.application_multus_ip(sid)
    http_port = int(p.get("http_port") or 8080)
    rtsp_port = int(p.get("rtsp_port") or app_cfg.server_port or 8555)
    console_ip = ott_console_ip(idx)
    console_mac = ott_console_mac(sid, idx)
    ue_name = f"oai-ue-slice-{sid}-client-{idx}"
    raw_client = (app_cfg.client_image or "").strip()
    override = str(p.get("ue_console_image") or "").strip()
    if override:
        img = override
    elif "ue-console" in raw_client:
        img = raw_client
    else:
        img = OTT_UE_CONSOLE_IMAGE

    chromium_img = str(p.get("chromium_image") or OTT_CHROMIUM_IMAGE).strip()
    chrome_cli = (
        "--proxy-server=socks5://127.0.0.1:1080 "
        "--remote-debugging-port=9222 "
        "--remote-allow-origins=* "
        "--no-first-run "
        "--no-default-browser-check "
        "--disable-features=TranslateUI "
        "--autoplay-policy=no-user-gesture-required "
        "--disable-gpu "
        "https://www.youtube.com"
    )

    common = [
        {"name": "TARGET_SERVER_IP", "value": server_ip},
        {"name": "SERVER_RTSP_PORT", "value": str(rtsp_port)},
        {"name": "PDU_IFACE", "value": f"oaitun_ue{sid}"},
        {"name": "PDU_ROUTE_HOSTS", "value": server_ip},
        {"name": "PDU_WAIT_TIMEOUT", "value": "300"},
        {"name": "SLICE_ID", "value": str(sid)},
        {"name": "CLIENT_INDEX", "value": str(idx)},
        {"name": "PROFILE_NAME", "value": profile_name},
        {"name": "UE_NAME", "value": ue_name},
        {"name": "CONSOLE_IP", "value": console_ip},
        {"name": "CONSOLE_MAC", "value": console_mac},
        {"name": "SERVER_URL", "value": f"http://{server_ip}"},
        {"name": "DEFAULT_CHANNEL", "value": f"channel_{idx}"},
        {"name": "STREAMING_ENABLED", "value": str(p.get("streaming_enabled", "0"))},
        {"name": "CLIENT_ID", "value": f"ue{idx}"},
        {"name": "BACKEND_URL", "value": f"http://127.0.0.1:{OTT_BACKEND_PORT}"},
        {"name": "BACKEND_PORT", "value": str(OTT_BACKEND_PORT)},
        {"name": "FRONTEND_PORT", "value": str(OTT_CONSOLE_PORT)},
        {"name": "DASHBOARD_STATIC", "value": "/app/ue/static"},
        {"name": "PDU_SOCKS_PORT", "value": "1080"},
        {"name": "CHROME_CDP_HOST", "value": "127.0.0.1"},
        {"name": "CHROME_CDP_PORT", "value": "9222"},
        {"name": "CHROME_HTTP_URL", "value": f"https://{console_ip}/chrome/"},
        {"name": "HTTPS_PORT", "value": "443"},
        {"name": "HTTP_PORT", "value": "80"},
        {"name": "CHROME_UPSTREAM", "value": "http://127.0.0.1:3000"},
    ]
    resources = {
        "requests": {"cpu": "100m", "memory": "128Mi"},
        "limits": {"cpu": "1", "memory": "1Gi"},
    }
    backend = {
        "name": "backend",
        "image": img,
        "imagePullPolicy": "Always",
        "securityContext": {
            "privileged": True,
            "capabilities": {"add": ["NET_ADMIN"]},
        },
        "env": [
            *common,
            {"name": "CONSOLE_ROLE", "value": "backend"},
            {"name": "APP_NAME", "value": f"slice{sid}-ott-client-{idx}"},
            {"name": "METRICS_PORT", "value": str(p.get("client_metrics_port") or 9111)},
            {"name": "OTT_PLAY_MODE", "value": "chromium_5g"},
            {"name": "OTT_PLAY_QUALITY", "value": str(p.get("play_quality") or "4k")},
        ],
        "ports": [
            {"name": "backend", "containerPort": OTT_BACKEND_PORT},
            {"name": "metrics", "containerPort": int(p.get("client_metrics_port") or 9111)},
            {"name": "socks", "containerPort": 1080},
        ],
        "resources": resources,
    }
    frontend = {
        "name": "frontend",
        "image": img,
        "imagePullPolicy": "Always",
        "env": [
            *common,
            {"name": "CONSOLE_ROLE", "value": "frontend"},
        ],
        "ports": [{"name": "console", "containerPort": OTT_CONSOLE_PORT}],
        "resources": {
            "requests": {"cpu": "50m", "memory": "64Mi"},
            "limits": {"cpu": "500m", "memory": "256Mi"},
        },
    }
    chromium = {
        "name": "chromium",
        "image": chromium_img,
        "imagePullPolicy": "IfNotPresent",
        "securityContext": {
            "privileged": True,
            "seccompProfile": {"type": "Unconfined"},
        },
        "env": [
            {"name": "PUID", "value": "1000"},
            {"name": "PGID", "value": "1000"},
            {"name": "TZ", "value": "UTC"},
            {"name": "TITLE", "value": f"OTT UE {ue_name}"},
            {"name": "DISABLE_IPV6", "value": "true"},
            {"name": "PIXELFLUX_WAYLAND", "value": "false"},
            {"name": "CHROME_CLI", "value": chrome_cli},
            {"name": "CUSTOM_PORT", "value": "3000"},
            {"name": "CUSTOM_HTTPS_PORT", "value": "3001"},
            {"name": "SUBFOLDER", "value": "/chrome/"},
        ],
        "ports": [
            {"name": "chrome-http", "containerPort": 3000},
            {"name": "chrome-https", "containerPort": 3001},
            {"name": "cdp", "containerPort": 9222},
        ],
        "volumeMounts": [
            {"name": "chromium-config", "mountPath": "/config"},
            {"name": "dshm", "mountPath": "/dev/shm"},
        ],
        "resources": {
            "requests": {"cpu": "500m", "memory": "1Gi"},
            "limits": {"cpu": "4", "memory": "4Gi"},
        },
    }
    return [
        backend,
        frontend,
        chromium,
        *build_ue_metrics_sidecars(profile_name, app_cfg, client_index),
    ]


def build_client_metrics_container(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    client_index: int = 1,
) -> dict:
    """Build metrics exporter sidecar container for client inside the UE pod."""
    sid = app_cfg.slice_id
    app_type = app_cfg.app_type.lower()
    p = app_cfg.params or {}
    m_port = {
        "cctv": 9101,
        "physical_ai": 8001,
        "ott": 9111,
        "iot": 9106,
        "custom": 9107,
    }.get(app_type, 9101)
    if "client_metrics_port" in p:
        m_port = int(p["client_metrics_port"])
    elif app_type == "cctv" and client_index > 1:
        m_port = 9100 + client_index

    app_name = f"slice{sid}-{app_type}-client-{client_index}"
    return _influx_pusher_container(
        m_port,
        app_name,
        sid,
        profile_name,
        app_type,
        "edge",
        ctr_name="metrics-exporter",
    )


def build_client_rtt_probe_container(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    client_index: int = 1,
) -> dict:
    """Dedicated ICMP RTT sidecar (PDU ping). Separate from metrics-exporter."""
    sid = app_cfg.slice_id
    app_type = app_cfg.app_type.lower()
    app_name = f"slice{sid}-{app_type}-client-{client_index}"
    return _rtt_probe_container(
        app_name,
        sid,
        profile_name,
        app_type,
        "edge",
    )


def build_client_throughput_stats_container(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    client_index: int = 1,
) -> dict:
    """Dedicated oaitun UL/DL Mbps sidecar. Separate from metrics-exporter."""
    sid = app_cfg.slice_id
    app_type = app_cfg.app_type.lower()
    app_name = f"slice{sid}-{app_type}-client-{client_index}"
    return _throughput_stats_container(
        app_name,
        sid,
        profile_name,
        app_type,
        "edge",
    )


def build_ue_metrics_sidecars(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    client_index: int = 1,
) -> List[dict]:
    """metrics-exporter (scrape/NTP) + rtt-probe (ICMP) + throughput-statistics (oaitun)."""
    return [
        build_client_metrics_container(profile_name, app_cfg, client_index),
        build_client_rtt_probe_container(profile_name, app_cfg, client_index),
        build_client_throughput_stats_container(profile_name, app_cfg, client_index),
    ]


def patch_ue_pod_with_client(
    profile_name: str,
    sid: int,
    client_container: Optional[dict],
    metrics_container: Optional[dict],
) -> Tuple[bool, str]:
    """Inject or remove client sidecar container in oai-ue-{sid} deployment on edge cluster."""
    kc_edge = _kube_cmd("edge")
    ue_dep_name = f"oai-ue-{sid}"

    proc = subprocess.run(
        [*kc_edge, "get", "deployment", ue_dep_name, "-n", profile_name, "-o", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return False, f"Deployment {ue_dep_name} not found on edge cluster: {proc.stderr}"

    try:
        dep = json.loads(proc.stdout)
    except Exception as e:
        return False, f"Failed to parse deployment {ue_dep_name}: {e}"

    containers = dep.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    
    # Remove existing app client sidecars or metrics exporter from UE pod
    containers = [
        c for c in containers
        if c.get("name") not in (
            "cctv-publisher", "aiperf", "ott-client", "iot-client", "custom-client",
            "backend", "frontend",
            "metrics-exporter", "rtt-probe", "throughput-statistics",
            f"app-client-metrics-{sid}",
        )
    ]

    volumes = dep.get("spec", {}).get("template", {}).get("spec", {}).get("volumes", [])

    if client_container is not None:
        containers.append(client_container)
        if client_container.get("name") == "cctv-publisher":
            cm_name = f"slice{sid}-cctv-client-code"
            pub_path = "/home/fcp/INA-Infra/applications/server/cctv/client/publisher.py"
            if not os.path.exists(pub_path):
                pub_path = "/home/fcp/INA-Infra/applications/cctv/client/publisher.py"
            pub_code = ""
            if os.path.exists(pub_path):
                with open(pub_path, "r", encoding="utf-8") as f:
                    pub_code = f.read()

            cm_manifest = {
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": cm_name,
                    "namespace": profile_name,
                },
                "data": {
                    "publisher.py": pub_code
                }
            }
            subprocess.run(
                [*kc_edge, "apply", "-f", "-"],
                input=yaml.dump_all([cm_manifest]),
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )

            volumes = [v for v in volumes if v.get("name") not in ("cctv-client-code", "cctv-data")]
            volumes.extend([
                {
                    "name": "cctv-client-code",
                    "configMap": {"name": cm_name, "defaultMode": 0o755},
                },
                {
                    "name": "cctv-data",
                    "emptyDir": {},
                },
            ])

    if metrics_container is not None:
        containers.append(metrics_container)

    patch_doc = {
        "spec": {
            "template": {
                "spec": {
                    "hostPID": True,
                    "containers": containers,
                    "volumes": volumes,
                }
            }
        }
    }

    apply_proc = subprocess.run(
        [*kc_edge, "patch", "deployment", ue_dep_name, "-n", profile_name, "--type=merge", "-p", json.dumps(patch_doc)],
        capture_output=True,
        text=True,
        check=False,
    )
    if apply_proc.returncode != 0:
        return False, apply_proc.stderr or "Patch failed"

    return True, f"Successfully injected client sidecar into {ue_dep_name}"


def generate_manifests(
    profile_name: str,
    app_cfg: SliceApplicationConfig,
    target_cluster: str,
    profile_subnet: Optional[str] = None,
) -> List[dict]:
    """Alias for generate_server_manifests for backwards compatibility."""
    return generate_server_manifests(profile_name, app_cfg, target_cluster, profile_subnet=profile_subnet)


def deploy_application_stream(
    profile_name: str,
    slice_id: Optional[int] = None,
    config: Optional[SliceApplicationConfig] = None,
    applications: Optional[Dict[str, SliceApplicationConfig]] = None,
) -> Iterator[str]:
    """Deploy UE client sidecar(s) on the edge cluster. Application servers are GitOps (PL Deploy)."""
    rec = profile_store.get_profile(profile_name)
    if rec is None:
        yield error_event(f"Profile '{profile_name}' not found")
        return

    if applications:
        rec = profile_store.save_profile_applications(profile_name, applications)

    apps = dict(rec.applications or {})
    if slice_id is not None:
        if config is not None:
            apps[str(slice_id)] = config
            rec = profile_store.save_profile_applications(profile_name, apps)
            apps = dict(rec.applications or apps)
        target_apps = [apps.get(str(slice_id))] if str(slice_id) in apps else []
    else:
        target_apps = [
            cfg
            for cfg in apps.values()
            if cfg.enabled and cfg.app_type.lower() != "none"
        ]

    if not target_apps or not any(target_apps):
        yield error_event(f"No active applications configured to deploy")
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    deployed_configs: List[SliceApplicationConfig] = []

    yield status_event(
        f"Deploying {len(target_apps)} UE client workload(s) in profile '{profile_name}'…"
    )

    for app in target_apps:
        if app is None or app.app_type.lower() == "none":
            continue

        sid = app.slice_id
        target_cluster = resolve_target_cluster(app, rec.pl_result)
        app_name = app.name or f"Slice {sid} ({app.app_type})"
        server_ip = site_ips.application_multus_ip(sid)

        # Servers are GitOps (PL Deploy → gitea_apply). Direct apply is client UEs only.
        yield log_event(
            "stdout",
            f"=== Client UEs for {app_name} (server via GitOps on '{target_cluster}', IP {server_ip}) ===",
        )

        # ── Deploy Application Client UE(s) on Edge Cluster directly via K8s API ──
        kc_edge = _kube_cmd("edge")
        client_count = int(
            app.params.get("client_count")
            or app.params.get("client_replicas")
            or 1
        )
        yield log_event(
            "stdout",
            f"=== Deploying {client_count} UE Client workload(s) for slice {sid} on edge cluster ===",
        )

        ip_plan = rec.pl_result.ip_plan if (rec.pl_result and rec.pl_result.ip_plan) else None
        if ip_plan is None and rec.profile and rec.pl_result and rec.pl_result.slices:
            from app.schemas import SliceIn
            s_in = [
                SliceIn(
                    id=s.id,
                    t_bar=s.t_bar,
                    d_bar=s.d_bar,
                    h_s=s.h_s,
                    eta_t0=s.eta_t0,
                    slice_type=s.slice_type,
                )
                for s in rec.pl_result.slices
            ]
            ip_plan = ip_allocator.allocate_profile_ips(
                rec.profile, s_in, rec.pl_result.deploy_map
            )

        sl = next((s for s in ip_plan.slices if s.n == sid), None) if ip_plan else None

        if sl and ip_plan:
            from app.services import paths as ina_paths

            mysql_script = ina_paths.profile_patch_mysql_script()
            if mysql_script.is_file():
                mysql_proc = subprocess.run(
                    [
                        "bash",
                        str(mysql_script),
                        profile_name,
                        "--slices",
                        str(ip_plan.n_slices),
                    ],
                    cwd=str(ina_paths.ina_infra_root()),
                    env=ina_paths.script_env(),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
                if mysql_proc.returncode == 0:
                    yield log_event(
                        "stdout",
                        "  [udr] provisioned per-UE IMSIs in MySQL "
                        f"(N={ip_plan.n_slices}, clients={client_count})",
                    )
                else:
                    tail = (mysql_proc.stderr or mysql_proc.stdout or "").strip().splitlines()
                    yield log_event(
                        "stderr",
                        f"  [udr] mysql IMSI patch warn: {tail[-1] if tail else mysql_proc.returncode}",
                    )

            client_volumes = None
            if app.app_type == "cctv":
                client_volumes = [
                    {
                        "name": "cctv-data",
                        "emptyDir": {},
                    },
                ]

            for c_idx in range(1, client_count + 1):
                if app.app_type == "cctv":
                    client_containers = build_cctv_ue_containers(
                        profile_name, app, server_ip=server_ip, client_index=c_idx
                    )
                elif app.app_type == "physical_ai":
                    client_containers = build_physical_ai_ue_containers(
                        profile_name, app, server_ip=server_ip, client_index=c_idx
                    )
                elif app.app_type == "ott":
                    client_containers = build_ott_ue_containers(
                        profile_name, app, server_ip=server_ip, client_index=c_idx
                    )
                    client_volumes = build_ott_ue_volumes()
                elif app.app_type == "iot":
                    client_containers = build_iot_ue_containers(
                        profile_name, app, server_ip=server_ip, client_index=c_idx
                    )
                else:
                    client_ctr = build_client_container(
                        profile_name, app, server_ip=server_ip, client_index=c_idx
                    )
                    metrics_sidecars = build_ue_metrics_sidecars(
                        profile_name, app, client_index=c_idx
                    )
                    client_containers = [c for c in [client_ctr, *metrics_sidecars] if c]

                if app.app_type == "cctv":
                    _src, _url, vlabel = cctv_videos.clip_for_client(
                        app.params or {}, c_idx
                    )
                    yield log_event(
                        "stdout",
                        f"  [client/edge] camera {c_idx} video={vlabel} ({_src})",
                    )
                console_ip = None
                console_mac = None
                console_port = None
                if app.app_type == "cctv":
                    console_ip = cctv_console_ip(c_idx, sid)
                    console_mac = cctv_console_mac(sid, c_idx)
                    console_port = CCTV_CONSOLE_PORT
                    yield log_event(
                        "stdout",
                        f"  [client/edge] UE {c_idx} console {console_ip} mac={console_mac}",
                    )
                elif app.app_type == "physical_ai":
                    console_ip = physical_ai_console_ip(c_idx)
                    console_mac = physical_ai_console_mac(sid, c_idx)
                    console_port = PHYSICAL_AI_CONSOLE_PORT
                    yield log_event(
                        "stdout",
                        f"  [client/edge] UE {c_idx} console {console_ip} mac={console_mac}",
                    )
                elif app.app_type == "ott":
                    console_ip = ott_console_ip(c_idx)
                    console_mac = ott_console_mac(sid, c_idx)
                    console_port = OTT_CONSOLE_PORT
                    yield log_event(
                        "stdout",
                        f"  [client/edge] UE {c_idx} console {console_ip} mac={console_mac}",
                    )
                elif app.app_type == "iot":
                    console_ip = iot_console_ip(c_idx, sid)
                    console_mac = iot_console_mac(sid, c_idx)
                    console_port = IOT_CONSOLE_PORT
                    yield log_event(
                        "stdout",
                        f"  [client/edge] UE {c_idx} console {console_ip} mac={console_mac}",
                    )
                ue_manifests = ran_workloads.generate_ue_manifests(
                    namespace=profile_name,
                    sl=sl,
                    shared=ip_plan.shared,
                    ip_plan=ip_plan,
                    ue_node=rec.profile.ue_node if rec.profile else "usrp",
                    client_containers=client_containers,
                    client_volumes=client_volumes,
                    client_index=c_idx,
                    console_ip=console_ip,
                    console_mac=console_mac,
                    console_port=console_port or PHYSICAL_AI_CONSOLE_PORT,
                )

                ue_proc = subprocess.run(
                    [*kc_edge, "apply", "-f", "-"],
                    input=yaml.dump_all(ue_manifests),
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                )
                ue_name = f"oai-ue-slice-{sid}-client-{c_idx}"
                if ue_proc.returncode == 0:
                    yield log_event("stdout", f"  [client/edge] ✔ Deployed UE & Client {ue_name}")
                else:
                    yield log_event("stderr", f"  [client/edge] ✖ Failed deploying {ue_name}: {ue_proc.stderr}")
        else:
            yield log_event("stderr", f"  [client/edge] ⚠ Could not resolve IP plan for slice {sid}")

        yield log_event("stdout", f"✔ Successfully deployed UE client(s) for {app_name}")
        profile_store.update_application_deploy_status(
            profile_name,
            sid,
            deployed=True,
            deployed_at=now_iso,
            last_error=None,
        )
        updated_app = app.model_copy(
            update={"deployed": True, "deployed_at": now_iso, "last_error": None}
        )
        deployed_configs.append(updated_app)

    updated_rec = profile_store.get_profile(profile_name)
    yield result_event(
        AppDeployResponse(
            ok=len(deployed_configs) > 0,
            message=f"Deployed {len(deployed_configs)} UE client workload(s)",
            deployed_apps=deployed_configs,
            profile=updated_rec,
        )
    )


def undeploy_application_stream(
    profile_name: str,
    slice_id: Optional[int] = None,
) -> Iterator[str]:
    """Undeploy UE client workload(s). Application servers remain until PL Undeploy (GitOps)."""
    rec = profile_store.get_profile(profile_name)
    if rec is None:
        yield error_event(f"Profile '{profile_name}' not found")
        return

    apps = dict(rec.applications or {})
    if slice_id is not None:
        target_sids = [slice_id] if str(slice_id) in apps else []
    else:
        target_sids = [cfg.slice_id for cfg in apps.values()]

    if not target_sids:
        yield error_event("No applications to undeploy")
        return

    yield status_event(
        f"Undeploying {len(target_sids)} UE client workload(s) in profile '{profile_name}'…"
    )

    undeployed_ids: List[int] = []

    for sid in target_sids:
        app_cfg = apps.get(str(sid))
        app_type = app_cfg.app_type if app_cfg else ""

        yield log_event(
            "stdout",
            f"=== Undeploying slice {sid} UE clients ({app_type}) ===",
        )

        # Client UEs only (ina.lab/role=client). Servers stay until PL Undeploy.
        kc_edge = _kube_cmd("edge")
        subprocess.run(
            [
                *kc_edge,
                "-n",
                profile_name,
                "delete",
                "deployment,sa,cm",
                "-l",
                f"ina.lab/role=client,ina.lab/slice={sid}",
                "--ignore-not-found=true",
                "--wait=false",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            [
                *kc_edge,
                "-n",
                profile_name,
                "delete",
                "cm",
                f"slice{sid}-cctv-client-code",
                "--ignore-not-found=true",
                "--wait=false",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        yield log_event(
            "stdout",
            f"  [edge] Removed client UEs for slice {sid} (GitOps will restore oai-ue-{sid})",
        )

        profile_store.update_application_deploy_status(
            profile_name,
            sid,
            deployed=False,
            deployed_at=None,
            last_error=None,
        )
        undeployed_ids.append(sid)
        yield log_event("stdout", f"✔ Undeployed slice {sid} UE clients")

    updated_rec = profile_store.get_profile(profile_name)
    yield result_event(
        AppUndeployResponse(
            ok=True,
            message=f"Undeployed {len(undeployed_ids)} UE client workload(s)",
            undeployed_apps=undeployed_ids,
            profile=updated_rec,
        )
    )
