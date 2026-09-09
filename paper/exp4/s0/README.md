# Exp4 Scheme 0 (S0) — static baseline

Dedicated deploy package for **S0**: PL off, PM frozen at peak \(\bar T\),
PS off (equal PRB, `nws-xapp` replicas = 0). Namespace: **`exp4-s0`**.

This is the waterfall’s 100% bar. Every slice has **CU-UP + UPF at
central** and **APP at edge** (N6 hairpin). Same five DL apps and IPs as
S1; only sites differ.

| Slice | DL app | CU-UP / UPF / APP | N6 IP | UE console |
| :---: | :--- | :--- | :--- | :--- |
| 1 | iperf3 + SFTP 5 MB | C / C / **E** | `10.1.137.211` | http://10.1.137.221/ |
| 2 | YOLO bbox (CCTV) | C / C / **E** | `10.1.137.212` | http://10.1.137.222/ |
| 3 | OTT / gstreamer watch | C / C / **E** | `10.1.137.213` | http://10.1.137.223/ |
| 4 | CPU offload | C / C / **E** | `10.1.137.214` | http://10.1.137.224/ |
| 5 | MQTT Get | C / C / **E** | `10.1.137.215` | http://10.1.137.225/ |

See [`../README.md`](../README.md) for the S0–S3 matrix.

## Deploy

```bash
cd /home/fcp/INA-Infra
export KUBECONFIG=~/.kube/config:~/.kube/config-central:~/.kube/config-regional:~/.kube/config-edge

python3 paper/exp4/s0/deploy.py
python3 paper/exp4/s0/deploy.py --no-push

./scripts/check-configsync.sh
python3 paper/exp4/s0/deploy_ue.py            # all five UEs
python3 paper/exp4/s0/deploy_ue.py --ue 5     # MQTT only
python3 paper/exp4/s0/deploy_ue.py 1,2,3,4
```

Confirm S0 hairpin: UPF pods on **central**, APP pods on **edge**.

## Undeploy

```bash
python3 paper/exp4/s0/undeploy.py
python3 paper/exp4/s0/undeploy.py --ue-only
python3 paper/exp4/s0/deploy_ue.py --undeploy --ue 1,2,3,4
python3 paper/exp4/s0/undeploy_ue.py --ue 1,2,3,4
```

Do not run this while `exp1-*` or another `exp4-s*` scheme is live on the same RAN.
