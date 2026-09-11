# Exp4 Scheme 2 (S2) — +PL +PM (resource control)

Dedicated deploy package for **S2**: same PL sites as S1, **PM on**
(`compute_policy: pm-resize`), PS off (equal PRB, `nws-xapp` replicas = 0).
Namespace: **`exp4-s2`**.

Copied from S1 (same workloads, IPs, console IPs, placement). PM sizes
APP **requests** from S1 measured usage × 1.25 (run `20260909-110400_300s`).
Limits still burst to 8 CPU / 8Gi. GPU stays 1 on slice 2.

| Slice | S1 mean CPU / RAM | S2 request |
| :---: | :--- | :--- |
| 1 FTP | 204m / 240Mi | 300m / 320Mi |
| 2 YOLO | 5627m / 1622Mi / 8.9% GPU | 7.1 CPU / 2Gi / 1 GPU |
| 3 OTT | 65m / 94Mi | 100m / 128Mi |
| 4 CPU-OFF | 372m / 239Mi | 500m / 320Mi |
| 5 MQTT | 818m / 106Mi | 1.05 / 192Mi |

| Slice | DL app | CU-UP / UPF / APP | N6 IP | UE console |
| :---: | :--- | :--- | :--- | :--- |
| 1 | iperf3 + SFTP 5 MB | C / C / **C** | `10.1.137.211` | http://10.1.137.221/ |
| 2 | YOLO bbox (CCTV) | **E / E / E** | `10.1.137.212` | http://10.1.137.222/ |
| 3 | OTT / gstreamer watch | **R / R / R** | `10.1.137.213` | http://10.1.137.223/ |
| 4 | CPU offload | C / C / **C** | `10.1.137.214` | http://10.1.137.224/ |
| 5 | MQTT Get | C / C / **C** | `10.1.137.215` | http://10.1.137.225/ |

See [`../README.md`](../README.md) for the full S0–S3 matrix.

## Deploy

```bash
cd /home/fcp/INA-Infra
export KUBECONFIG=~/.kube/config:~/.kube/config-central:~/.kube/config-regional:~/.kube/config-edge

# Render + copy into repos/ + push Gitea (Config Sync)
python3 paper/exp4/s2/deploy.py

# Dry run (no Gitea push)
python3 paper/exp4/s2/deploy.py --no-push

# After RootSync is healthy:
./scripts/check-configsync.sh
python3 paper/exp4/s2/deploy_ue.py            # all five UEs
python3 paper/exp4/s2/deploy_ue.py --ue 5     # MQTT only
python3 paper/exp4/s2/deploy_ue.py 1,2,3,4
```

Confirm S2 co-location (same as S1): slice 1/4/5 APP+UPF on **central**,
slice 2 on **edge**, slice 3 on **regional**. Namespace labels
`ina.lab/pm=true` and ConfigMap `compute_policy: pm-resize`.

## Undeploy

```bash
python3 paper/exp4/s2/undeploy.py           # UEs + GitOps ns + cluster ns
python3 paper/exp4/s2/undeploy.py --ue-only
python3 paper/exp4/s2/undeploy.py --no-push
```

Do not run this while `exp1-*` or another `exp4-s*` scheme is live on the same RAN.
