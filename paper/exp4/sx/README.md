# Exp4 Scheme X (SX) — isolated per-slice traffic

Copied from **S1** (+PL, frozen peak compute, PS off). Namespace: **`exp4-sx`**.

GitOps is the S1 layout (all five apps + CU-UP/UPF). **UEs attach one
slice at a time** so that slice has the cell to itself. Mean DL goodput
is the uncontended traffic requirement \(T\) used to set \(\bar T\).

| Slice | DL app | CU-UP / UPF / APP | N6 IP | UE console |
| :---: | :--- | :--- | :--- | :--- |
| 1 | iperf3 + SFTP | C / C / **C** | `10.1.137.211` | http://10.1.137.221/ |
| 2 | YOLO bbox (CCTV) | **E / E / E** | `10.1.137.212` | http://10.1.137.222/ |
| 3 | OTT / gstreamer watch | **R / R / R** | `10.1.137.213` | http://10.1.137.223/ |
| 4 | CPU offload | C / C / **C** | `10.1.137.214` | http://10.1.137.224/ |
| 5 | MQTT Get | C / C / **C** | `10.1.137.215` | http://10.1.137.225/ |

Do not run this while another `exp4-s*` scheme is live on the same RAN.

## Deploy GitOps (once)

```bash
cd /home/fcp/INA-Infra
export KUBECONFIG=~/.kube/config:~/.kube/config-central:~/.kube/config-regional:~/.kube/config-edge

python3 paper/exp4/sx/deploy.py
./scripts/check-configsync.sh
```

## Measure each slice

`measure.py` deploys **one UE**, waits until the PDU is on 5G and the app is
producing DL traffic for `--settle` seconds, then records Influx. It tears
that UE down before the next slice.

Stable means:

| Slice | Ready when |
| :---: | :--- |
| 1 FTP / 4 CPU-OFF | SFTP `running` and `success` increasing |
| 2 YOLO | `pdu_ready` and annotated RTSP bytes growing (UL publish stays off) |
| 3 OTT | SOCKS `bytes_down` increasing |
| 5 MQTT | `mqtt_connected` and `dl_mbps` > 0.2 |

```bash
# All five, 300s each after 30s stable
python3 paper/exp4/sx/measure.py --duration 300 --settle 30

# One slice
python3 paper/exp4/sx/measure.py --ue 1 --settle 45

python3 paper/exp4/sx/measure.py --ue 2,3 --duration 5m
```

Writes under `paper/exp4/sx/data/` (gitignored):

- `slice<N>_<timestamp>_<duration>/` — Influx CSVs + SLA summary for that slice
- `traffic_requirements.json` / `.csv` — mean delay and Mbps per slice

## Undeploy

```bash
python3 paper/exp4/sx/undeploy.py           # UEs + GitOps ns
python3 paper/exp4/sx/undeploy.py --ue-only
```
