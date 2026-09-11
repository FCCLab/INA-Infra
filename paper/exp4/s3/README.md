# Exp4 Scheme 3 (S3) — +PL +PM +PS

Dedicated deploy package for **S3**: same PL sites and PM requests as S2,
**PS on** (`prb_policy: live-eta`, `nws-xapp` replicas = 1). Namespace:
**`exp4-s3`**.

Compute is the S2 PM resize (S1 usage × 1.25). Radio floors are **DL-only** (`dl_scheduler_type=1` NS, `ul_scheduler_type=0`
PF). DL floors are **20 / 20 / 20 / 20 / 10** % (slices 1–5, sum 90%).
UL is not partitioned (TCP ACKs stay PF). Dedicated stays 0; DL max stays 100.

| Slice | \(\bar T\) | min PRB % | PM request |
| :---: | ---: | ---: | :--- |
| 1 FTP | 20.2 | **20.0** | 300m / 320Mi |
| 2 YOLO | 16.8 | **20.0** | 7.1 CPU / 2Gi / 1 GPU |
| 3 OTT | 56.9 | **20.0** | 100m / 128Mi |
| 4 CPU-OFF | 18.3 | **20.0** | 500m / 320Mi |
| 5 MQTT | 3.67 | **10.0** | 1.05 / 192Mi |
| **sum** | | **90.0** | |

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

python3 paper/exp4/s3/deploy.py
python3 paper/exp4/s3/deploy.py --no-push

./scripts/check-configsync.sh
python3 paper/exp4/s3/deploy_ue.py
python3 paper/exp4/s3/deploy_ue.py --ue 5
```

Confirm `ina.lab/ps=true`, ConfigMap `prb_policy: live-eta`, DU
`ul_scheduler_type=0` and `dl_min_prb_ratio` as the table, and `nws-xapp`
replicas = 1.

## Undeploy

```bash
python3 paper/exp4/s3/undeploy.py
python3 paper/exp4/s3/undeploy.py --ue-only
python3 paper/exp4/s3/undeploy.py --no-push
```

Do not run this while another `exp4-s*` scheme is live on the same RAN.
