# Experiment 4 applications

Dedicated downlink slice apps. Do not mix with Exp1 `applications/servers/` or `applications/clients/`.

Each pod has **two Multus ifaces**: simulated 5G (`net1`, `10.140.<N>.x`) and console (`net2`, `10.1.137.x`).

| Dir | Slice | Sim5G server | Sim5G client | Consoles |
| :--- | :---: | :--- | :--- | :--- |
| [`exp4_s1_iperf_sftp`](exp4_s1_iperf_sftp/) | 1 | `10.140.1.1` | `10.140.1.2` | [server](http://10.1.137.211/) · [client](http://10.1.137.221/) |
| [`exp4_s2_cctv`](exp4_s2_cctv/) | 2 | `10.140.2.1` | `10.140.2.2` | [server](http://10.1.137.212/) · [client](http://10.1.137.222/) |
| [`exp4_s3_ott`](exp4_s3_ott/) | 3 | `10.140.3.1` | `10.140.3.2` | [server](http://10.1.137.213/) · [client](http://10.1.137.223/) |
| [`exp4_s4_cpu_offload`](exp4_s4_cpu_offload/) | 4 | `10.140.4.1` | `10.140.4.2` | [server](http://10.1.137.214/) · [client](http://10.1.137.224/) |
| [`exp4_s5_iot`](exp4_s5_iot/) | 5 | `10.140.5.1` | `10.140.5.2` | [server](http://10.1.137.215/) · [client](http://10.1.137.225/) |

Full addressing: [`ip_plan.md`](ip_plan.md).

Two images per app: `exp4-s<N>-<name>-server` and `exp4-s<N>-<name>-client`. Backend and frontend console run as two processes in that image (`:8080`/`:8090` + `:80`).

---

## Usage (simulated 5G, no RAN)

Two Multus macvlans on the site parent NIC. No `oaitun`. Data is `net1` `10.140.<N>.0/24`; consoles stay on `net2` `10.1.137.0/24`.

Default cluster is **edge** (`gpu-a40` / Multus `ens12f0`), namespace **`exp4-apps`**. Images: **`nws-v0.4-amd64`** (s1/s3/s4/s5), **`nws-v0.6-amd64`** (s2 CCTV). Slice 2 requests the A40.

### 1. Build and push

```bash
# all five slices → 10.1.132.30:5000
IMAGE_TAG=nws-v0.4-amd64 ./applications/exp4/build_images.sh --push

# subset
IMAGE_TAG=nws-v0.4-amd64 ./applications/exp4/build_images.sh --push s1 s4
```

If Docker rejects the registry cert:

```bash
sudo ./scripts/setup-docker-insecure-registry.sh 10.1.132.30:5000
```

### 2. Show the IP plan

```bash
./applications/exp4/exp4_deploy.sh --plan
```

### 3. Deploy

Wipe the namespace and bring everything back (self-contained: sim5G NAT, routes, CCTV engine mount):

```bash
./applications/exp4/exp4_deploy.sh --undeploy && ./applications/exp4/exp4_deploy.sh
```

```bash
# all slices (per-slice newest tags) without deleting first
./applications/exp4/exp4_deploy.sh

# one or more slices
./applications/exp4/exp4_deploy.sh s1
./applications/exp4/exp4_deploy.sh s2 s3 s5

# pin one tag for every slice
IMAGE_TAG=nws-v0.4-amd64 ./applications/exp4/exp4_deploy.sh s1

# another cluster (VMs, Multus enp7s0)
CLUSTER=central ./applications/exp4/exp4_deploy.sh s4
```

Do not start Exp1 slice apps at the same time: they reuse `.211`–`.214` and overlap the `.221`–`.225` consoles.

### 4. Check

```bash
./applications/exp4/exp4_deploy.sh --status

kubectl --context=edge@edge -n exp4-apps get pods -o wide
kubectl --context=edge@edge -n exp4-apps logs -l ina.lab/role=server --tail=50
```

Open the console URLs in the table (port 80 on the **137** address, `net2`). App data uses `10.140.<N>.1` ↔ `.2` on `net1`.

| Slice | What to try on the console |
| :---: | :--- |
| 1 | Client: SFTP download / `iperf3 -R` |
| 2 | Server: DeepStream YOLO ×N MediaMTX; client: 2×2 annotated DL |
| 3 | Server: channels / UEs; client: watch DL |
| 4 | Server: generate→encrypt queues; client: continuous download / delete |
| 5 | Server: Publish DL MQTT (~2 Mbps); client: subscribe only (no UL) |

### 5. Tear down

```bash
./applications/exp4/exp4_deploy.sh --undeploy
# or only one cluster/ns
CLUSTER=edge NAMESPACE=exp4-apps ./applications/exp4/exp4_deploy.sh --undeploy
```

`--undeploy` deletes namespace `exp4-apps`. CCTV TensorRT engines stay on the node at `/var/lib/ina-infra/exp4-s2-models` so the next deploy does not rebuild them.

### Deploy flags and env

| Flag / env | Meaning | Default |
| :--- | :--- | :--- |
| `s1`…`s5` | Slice subset | all |
| `--plan` | Print IP table only | |
| `--status` | Plan + pods/NADs | |
| `--undeploy` | Delete namespace | |
| `IMAGE_TAG` | Override tag for every slice | s1/s3/s4/s5 `nws-v0.4-amd64`, s2 `nws-v0.6-amd64` |
| `CLUSTER` | `edge` / `central` / `regional` | `edge` |
| `NODE_NAME` | Pin hostname | `gpu-a40` on edge |
| `MULTUS_MASTER` | Multus parent NIC | `ens12f0` on edge, `enp7s0` elsewhere |
| `NAMESPACE` | Kubernetes namespace | `exp4-apps` |
| `REGISTRY` | Image registry | `10.1.132.30:5000` |

---

## Images (2 per app)

| Image | Slice |
| :--- | :---: |
| `exp4-s1-iperf-sftp-server` / `exp4-s1-iperf-sftp-client` | 1 |
| `exp4-s2-cctv-server` / `exp4-s2-cctv-client` | 2 |
| `exp4-s3-ott-server` / `exp4-s3-ott-client` | 3 |
| `exp4-s4-cpu-offload-server` / `exp4-s4-cpu-offload-client` | 4 |
| `exp4-s5-iot-server` / `exp4-s5-iot-client` | 5 |

Slice 5 server includes Mosquitto (`:1883` OTA, `:1884` local).

Build without push: `./applications/exp4/build_images.sh` (default tag `nws-v0.4-amd64` unless `IMAGE_TAG` is set).

---

## Backend + frontend console

Same image, two processes:

| Process | Path | Port | Role |
| :--- | :--- | :--- | :--- |
| Backend | `server/` or `client/backend/` | server `:8080`, client `:8090` | Workload |
| Frontend console | `*/frontend-console/` | `:80` | UI; proxies `/api/*` to the backend |

## Interfaces

| Role | Env | Simulated 5G (`exp4_deploy.sh`) | Later with real 5G |
| :--- | :--- | :--- | :--- |
| Server data | `TO_CLIENT_IFACE` | `net1` (`10.140.<N>.1`) | `net1` (`10.1.137.21N` N6) |
| Server console | `CONSOLE_IFACE` | `net2` (`10.1.137.21N`) | `eth0` (optional) |
| Client data | `TO_SERVER_IFACE` | `net1` (`10.140.<N>.2`) | `oaitun_ue1` (`10.140.<N>.2`) |
| Client console | `CONSOLE_IFACE` | `net2` (`10.1.137.22N`) | `net1` (`.22N`) |

## Metrics

Each **server** publishes absolute **CPU millicores / RAM MB / GPU % / VRAM MB**
(`origin=server`; `1000m` = one full CPU; `gpu_pct` = 0–100% of one GPU).
Each **client** publishes **DL throughput** (RX on `TO_SERVER_IFACE` / `net1`) and **latency**
(ping to server sim5G IP `10.140.<N>.1` via `TO_SERVER_IFACE`, `origin=client`).

Influx in-cluster `http://influxdb.influxdb.svc:8086`, measurement `application_metrics`.

Grafana JSON: each app’s `dashboard/` (4 server + 2 client panels). Import at `http://10.1.137.105:3000`.

```bash
python3 applications/exp4/common/generate_dashboards.py
```

5G scheme GitOps (not this no-5G path): `python3 paper/exp4/s0/deploy.py` (and `s1`–`s3`).
