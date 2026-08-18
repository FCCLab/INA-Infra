"""Site-L2 Multus addresses for application servers and UE consoles.

Outside Glass DHCP (10.1.137.160–199). Gateway 10.1.137.1 for operator reachability
via the site router; cluster traffic stays on eth0 via policy routing (from MULTUS_IP).
"""

from __future__ import annotations

SITE_NET = "10.1.137"
SITE_GW = "10.1.137.1"
SITE_PREFIX = 24

# Application servers (N6 / web consoles): slice 1 → .211 … slice 4 → .214
APP_SERVER_OCTET_BASE = 210

# UE consoles on usrp: 10 per slice starting at .220. Slice 4 would run past
# .254 (last usable /24 host) and .255 (broadcast), so UEs 6+ spill into the
# unused block after DHCP (.200–.209). See docs/ip_plan.md.
UE_CONSOLE_OCTET_BASE = 220
UE_CONSOLE_OVERFLOW_BASE = 200
UE_CONSOLE_OVERFLOW_LAST = 209
# Operator + UE web consoles (HTTP default; omit from URLs).
CONSOLE_PORT = 80
UE_CONSOLE_PORT = CONSOLE_PORT


def application_multus_ip(slice_id: int) -> str:
    return f"{SITE_NET}.{APP_SERVER_OCTET_BASE + int(slice_id)}"


def application_multus_mac(slice_id: int) -> str:
    return f"02:0a:89:a0:00:{int(slice_id):02x}"


def http_url(host: str, port: int | None = None) -> str:
    p = CONSOLE_PORT if port is None else int(port)
    if p == 80:
        return f"http://{host}/"
    return f"http://{host}:{p}/"


def application_console_port(app_type: str) -> int:
    return CONSOLE_PORT


def application_console_url(slice_id: int, app_type: str = "") -> str:
    return http_url(application_multus_ip(slice_id), application_console_port(app_type))


def ue_console_ip(slice_id: int, client_index: int = 1) -> str:
    idx = max(int(client_index), 1)
    octet = UE_CONSOLE_OCTET_BASE + (int(slice_id) - 1) * 10 + idx - 1
    if octet >= 255:
        # .255 is broadcast on 10.1.137.0/24; .256+ is not a host address.
        octet = UE_CONSOLE_OVERFLOW_BASE + (octet - 255)
    if 210 <= octet <= 219:
        raise ValueError(
            f"UE console {SITE_NET}.{octet} collides with the .210 spare or "
            f"application servers (.211–.214); slice {slice_id} UE {idx} exceeds "
            f"the overflow block {SITE_NET}.{UE_CONSOLE_OVERFLOW_BASE}–"
            f".{UE_CONSOLE_OVERFLOW_LAST}"
        )
    if octet < 1 or octet > 254:
        raise ValueError(f"UE console octet {octet} is not a usable host on {SITE_NET}.0/24")
    return f"{SITE_NET}.{octet}"


def ue_console_mac(slice_id: int, client_index: int = 1) -> str:
    return f"02:0a:40:{int(slice_id):02x}:00:{max(int(client_index), 1):02x}"


def multus_src_route_init(
    ip: str,
    iface: str = "net1",
    gw: str = SITE_GW,
) -> dict:
    """Policy default via site GW for packets sourced from the Multus IP (router access)."""
    script = f"""set -euo pipefail
IFACE="{iface}"
IP="{ip}"
GW="{gw}"
for _ in $(seq 1 40); do
  ip link show "$IFACE" >/dev/null 2>&1 && break
  sleep 1
done
ip link show "$IFACE"
# On-link site subnet in main + table 137. Table 137 must include the /24 or
# replies to UPF N6 (SNAT 10.1.137.x) go via the site GW and TCP times out.
ip route replace {SITE_NET}.0/{SITE_PREFIX} dev "$IFACE" || true
ip route replace {SITE_NET}.0/{SITE_PREFIX} dev "$IFACE" table 137 || true
ip route replace default via "$GW" dev "$IFACE" table 137 || true
ip rule del from "$IP"/32 table 137 2>/dev/null || true
ip rule add from "$IP"/32 table 137 priority 100
ip route show
ip route show table 137
ip rule show
"""
    return {
        "name": "multus-default-route",
        "image": "docker.io/nicolaka/netshoot",
        "imagePullPolicy": "Always",
        "securityContext": {"capabilities": {"add": ["NET_ADMIN"]}},
        "command": ["bash", "-c", script],
        "resources": {
            "requests": {"cpu": "10m", "memory": "16Mi"},
            "limits": {"cpu": "100m", "memory": "64Mi"},
        },
    }
