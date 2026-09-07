# Exp4 application IP plan

Two Multus macvlan attachments per pod on the site parent NIC
(`ens12f0` on edge A40, `enp7s0` on VMs). Same L2 as `10.1.137.0/24`
(gateway `10.1.137.1`). **Not** a real RAN PDU (`oaitun`); the data
plane uses PDU-like addresses on `net1`.

Authoritative site table: [`docs/ip_plan.md`](../../docs/ip_plan.md).
This file is what [`exp4_deploy.sh`](exp4_deploy.sh) uses.

## Pairing

One rule for every slice \(N = 1\ldots5\):

| Role | Simulated 5G (`net1`) | Console (`net2`) |
| :--- | :--- | :--- |
| Server | `10.140.N.1` | `10.1.137.21N` |
| Client | `10.140.N.2` | `10.1.137.22N` |

| Slice | App | Sim5G server | Sim5G client | Consoles |
| :---: | :--- | :--- | :--- | :--- |
| 1 | FTP | `10.140.1.1` | `10.140.1.2` | http://10.1.137.211/ · http://10.1.137.221/ |
| 2 | CCTV / YOLO | `10.140.2.1` | `10.140.2.2` | http://10.1.137.212/ · http://10.1.137.222/ |
| 3 | OTT | `10.140.3.1` | `10.140.3.2` | http://10.1.137.213/ · http://10.1.137.223/ |
| 4 | CPU offload | `10.140.4.1` | `10.140.4.2` | http://10.1.137.214/ · http://10.1.137.224/ |
| 5 | MQTT | `10.140.5.1` | `10.140.5.2` | http://10.1.137.215/ · http://10.1.137.225/ |

## Why two Multus ifaces

Without RAN/PDU, `oaitun_ue*` does not exist. Both pods attach:

| Iface | Plane | Addresses | Purpose |
| :--- | :--- | :--- | :--- |
| `net1` | Simulated 5G | `10.140.<N>.1` / `.2` | App data (iperf, RTSP, MQTT, YouTube via SOCKS). Client default via `.1`; server NAT. |
| `net2` | Console | `10.1.137.21N` / `.22N` | Operator UI on `:80`. Site GW `10.1.137.1`. |
| `eth0` | Cluster | Flannel | Influx `influxdb.influxdb.svc` / node network |

`net1` uses the same `10.140.<N>.0/24` numbering as a real DNN (GW `.1`, UE `.2`)
so client `TARGET_SERVER_IP` and throughput metrics look like PDU. Do not run
this path together with live OAI UEs on the same slice (PDU IP clash).

When real 5G is added later: keep `net2` 137 addresses as **console**; move the
client data plane to `TO_SERVER_IFACE=oaitun_ue1` / `10.140.<N>.2`. Server N6
stays `10.1.137.21N` on `net1`.

## Servers

| Slice | App | Sim5G (`net1`) | Console (`net2`) | Console MAC | Ports |
| :---: | :--- | :--- | :--- | :--- | :--- |
| 1 | FTP | `10.140.1.1` | `10.1.137.211` | `02:0a:89:a0:00:01` | `:80` console, `:8080` API, `:5201` iperf, `:22` SFTP |
| 2 | CCTV / YOLO | `10.140.2.1` | `10.1.137.212` | `02:0a:89:a0:00:02` | `:80` console, `:8080` API, `:8554` RTSP |
| 3 | OTT | `10.140.3.1` | `10.1.137.213` | `02:0a:89:a0:00:03` | `:80` console, `:8080` API, `:8554`/`:8555` RTSP |
| 4 | CPU offload | `10.140.4.1` | `10.1.137.214` | `02:0a:89:a0:00:04` | `:80` console, `:8080` `/download` |
| 5 | MQTT | `10.140.5.1` | `10.1.137.215` | `02:0a:89:a0:00:05` | `:80` console, `:8080` API, `:1883` MQTT |

Sim5G MACs: `02:0a:8b:a0:00:0N`.

## Clients

| Slice | Sim5G (`net1`) | Sim5G MAC | Console (`net2`) | Console MAC |
| :---: | :--- | :--- | :--- | :--- |
| 1 | `10.140.1.2` | `02:0a:41:01:00:01` | `10.1.137.221` | `02:0a:40:01:00:01` |
| 2 | `10.140.2.2` | `02:0a:41:02:00:01` | `10.1.137.222` | `02:0a:40:02:00:01` |
| 3 | `10.140.3.2` | `02:0a:41:03:00:01` | `10.1.137.223` | `02:0a:40:03:00:01` |
| 4 | `10.140.4.2` | `02:0a:41:04:00:01` | `10.1.137.224` | `02:0a:40:04:00:01` |
| 5 | `10.140.5.2` | `02:0a:41:05:00:01` | `10.1.137.225` | `02:0a:40:05:00:01` |

Exp4 first clients sit inside the slice-1 UE-console decade (`.221`–`.225`).
Do not run Exp1 UEs at the same time.

## Env (set by `exp4_deploy.sh`)

| Side | Env | Value |
| :--- | :--- | :--- |
| Both | `TO_*_IFACE` / `PDU_IFACE` | `net1` |
| Both | `CONSOLE_IFACE` | `net2` |
| Both | `SIM5G_IP` / `MULTUS_IP` | `10.140.<N>.1` or `.2` |
| Both | `CONSOLE_IP` | `10.1.137.21N` or `.22N` |
| Server | `E2E_PROBE_HOST` | client `10.140.<N>.2` |
| Client | `TARGET_SERVER_IP` | server `10.140.<N>.1` |

### Multus reply-path profiles

InitContainer `multus-route-profile` (`NET_ADMIN`):

```text
# Simulated 5G (table 140)
ip route  10.140.<N>.0/24 dev net1
ip route  10.140.<N>.0/24 dev net1 table 140
ip rule   from <SIM5G_IP>/32 lookup 140 priority 90
# Client only (UE → DNN GW, so SOCKS/YouTube can egress):
ip route  default via 10.140.<N>.1 dev net1 table 140
# Server only (privileged init): ip_forward + MASQUERADE 10.140.<N>.0/24

# Console (table 137)
ip route  10.1.137.0/24 dev net2
ip route  10.1.137.0/24 dev net2 table 137
ip route  default via 10.1.137.1 dev net2 table 137
ip rule   from <CONSOLE_IP>/32 lookup 137 priority 100
```

Main Flannel default (`via eth0`) stays for cluster traffic. Helper:
[`common/multus_route_profile.sh`](common/multus_route_profile.sh).

## Do not use (console `10.1.137.0/24`)

| Range | Why |
| :--- | :--- |
| `.1`, `.10`–`.22` | gateway / hypervisor bridges |
| `.101`–`.106` | OpenSpeedTest / Influx / Grafana / DynDNS |
| `.110`–`.134`, `.150`–`.152` | K8s nodes |
| `.160`–`.199` | Glass DHCP (UPF N6) |
| `.255` | broadcast |
